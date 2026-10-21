import argparse
import os
import shutil
import stat
import tempfile


COPY_BUFFER_SIZE = 1024 * 1024


def write_header(output, name, ino, mode, size=0, links=1):
    name_bytes = os.fsencode(name) + b"\x00"
    # newc uses 13 fixed-width 32-bit fields, root ownership and zero mtimes.
    fields = (ino, mode, 0, 0, links, 0, size, 0, 0, 0, 0, len(name_bytes), 0)
    if any(value < 0 or value > 0xffffffff for value in fields):
        raise ValueError(f"newc field exceeds 32 bits: {name}")
    output.write(b"070701" + "".join(f"{value:08x}" for value in fields).encode("ascii"))
    output.write(name_bytes)
    output.write(b"\x00" * (-(110 + len(name_bytes)) % 4))


def entries(directory, prefix=""):
    with os.scandir(directory) as iterator:
        children = sorted(iterator, key=lambda entry: entry.name)
    for child in children:
        name = f"{prefix}/{child.name}" if prefix else child.name
        info = child.stat(follow_symlinks=False)
        yield child.path, name, info
        if stat.S_ISDIR(info.st_mode):
            yield from entries(child.path, name)


def write_cpio(src_dir, out_file):
    source = os.path.realpath(src_dir)
    destination = os.path.realpath(out_file)
    if not os.path.isdir(source):
        raise NotADirectoryError(src_dir)
    if os.path.commonpath((source, destination)) == source:
        raise ValueError("archive output must be outside the source tree")
    directory = os.path.dirname(os.path.abspath(out_file))
    with tempfile.NamedTemporaryFile(prefix=".ramdisk-", dir=directory, delete=False) as output:
        temporary = output.name
        try:
            for ino, (path, name, info) in enumerate(entries(source), 1):
                if stat.S_ISDIR(info.st_mode):
                    # Write directory entries before their children.
                    write_header(output, name, ino, 0o40755, links=2)
                    continue
                if stat.S_ISLNK(info.st_mode):
                    data = os.fsencode(os.readlink(path))
                    write_header(output, name, ino, 0o120777, len(data))
                    output.write(data)
                    size = len(data)
                elif stat.S_ISREG(info.st_mode):
                    mode = 0o100755 if info.st_mode & 0o111 else 0o100644
                    size = info.st_size
                    write_header(output, name, ino, mode, size)
                    start = output.tell()
                    with open(path, "rb") as content_file:
                        shutil.copyfileobj(content_file, output, COPY_BUFFER_SIZE)
                    if output.tell() - start != size:
                        raise ValueError(f"source changed while archiving: {name}")
                else:
                    raise ValueError(f"unsupported ramdisk entry type: {name}")
                output.write(b"\x00" * (-size % 4))

            # Trailer, then standard 512-byte archive alignment.
            write_header(output, "TRAILER!!!", 0, 0)
            output.write(b"\x00" * (-output.tell() % 512))
            output.flush()
            os.fsync(output.fileno())
            output.close()
            os.replace(temporary, out_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write a deterministic Android newc ramdisk")
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args()
    write_cpio(args.source, args.output)
