import os, sys

def write_cpio(src_dir, out_file):
    with open(out_file, "wb") as out:
        ino = 1
        for root, dirs, files in os.walk(src_dir):
            dirs.sort()
            files.sort()
            rel_root = os.path.relpath(root, src_dir)
            if rel_root == ".":
                rel_root = ""
            
            # Write dir entry if not root
            if rel_root:
                name_bytes = rel_root.encode("utf-8") + b"\x00"
                namesize = len(name_bytes)
                mode = 0o40755
                filesize = 0
                hdr = f"070701{ino:08x}{mode:08x}{0:08x}{0:08x}{2:08x}{0:08x}{filesize:08x}{0:08x}{0:08x}{0:08x}{0:08x}{namesize:08x}{0:08x}".encode("ascii")
                out.write(hdr)
                out.write(name_bytes)
                pad1 = (4 - ((110 + namesize) % 4)) % 4
                out.write(b"\x00" * pad1)
                ino += 1

            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.join(rel_root, f) if rel_root else f
                name_bytes = rel_path.encode("utf-8") + b"\x00"
                namesize = len(name_bytes)
                st = os.stat(full_path)
                mode = 0o100644
                filesize = st.st_size
                hdr = f"070701{ino:08x}{mode:08x}{0:08x}{0:08x}{1:08x}{0:08x}{filesize:08x}{0:08x}{0:08x}{0:08x}{0:08x}{namesize:08x}{0:08x}".encode("ascii")
                out.write(hdr)
                out.write(name_bytes)
                pad1 = (4 - ((110 + namesize) % 4)) % 4
                out.write(b"\x00" * pad1)
                with open(full_path, "rb") as content_file:
                    out.write(content_file.read())
                pad2 = (4 - (filesize % 4)) % 4
                out.write(b"\x00" * pad2)
                ino += 1

        # Trailer
        trailer_name = b"TRAILER!!!\x00"
        namesize = len(trailer_name)
        hdr = f"070701{0:08x}{0:08x}{0:08x}{0:08x}{1:08x}{0:08x}{0:08x}{0:08x}{0:08x}{0:08x}{0:08x}{namesize:08x}{0:08x}".encode("ascii")
        out.write(hdr)
        out.write(trailer_name)
        pad = (4 - ((110 + namesize) % 4)) % 4
        out.write(b"\x00" * pad)
        # Pad to 512 bytes alignment if needed, standard cpio does:
        curr_pos = out.tell()
        pad512 = (512 - (curr_pos % 512)) % 512
        out.write(b"\x00" * pad512)

if __name__ == "__main__":
    write_cpio(sys.argv[1], sys.argv[2])
