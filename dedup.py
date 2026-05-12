#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

from perception import hashers

image_exts = [
	"bmp", "jpg", "jpeg", "png", "tif", "tiff", "webp",
	"jp2", "j2k", "jpf", "jpm", "jpg2", "j2c", "jpc", "jpx"
	]

# Pair of potential duplicates and the distance between them.
# `file1` and `file2` are stored as `str` rather than with the `File` class.
class Pair:
    def __init__(self, file1, file2, dist):
        self.file1 = file1
        self.file2 = file2
        self.dist = dist

# Store relevant information about a file.
class File:
    def __init__(self, filepath):
        self.filepath = filepath
        self.hash = None
        self.last_modified = os.path.getmtime(filepath)

# Sort pairs by distance and print.
def print_pairs(pairs):
    pairs.sort(key=lambda p: p.dist)
    for p in pairs:
        print(f"{p.file1}\t{p.file2}\t{p.dist}")

# Identify pairs of potential duplicates based on the parameters set by the user.
def find_dups(files, cli_args):
    hasher = hashers.PHash(hash_size=16)

    # Compute hashes but leave a hash of None if it could not be calculated.
    for file in files:
        try:
            file.hash = hasher.compute(file.filepath)
        except:
            if not cli_args.quiet:
                print(f"failed to compute hash for {file.filepath}", file=sys.stderr)

    # Calculate distance between all pairs of hashes and save those under the
    # threshold.
    pairs = []
    for i, file1 in enumerate(files):
        for file2 in files[i + 1:]:
            if file1.hash is None or file2.hash is None:
                continue
            distance = hasher.compute_distance(file1.hash, file2.hash)
            if distance <= cli_args.threshold:
                pairs.append(Pair(file1.filepath, file2.filepath, distance))

    return pairs

# Show one set of potential duplicates to the user and return the ones they
# select by index (to save).
def choose_with_viewer(dups, viewer_cmd):
    chosen = []
    # Split `viewer_cmd` into a list to pass to Popen and append `dups`.
    cmd = viewer_cmd.split() + dups
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    except:
        # Tell user the command failed to run so they can cancel if they wish.
        print(f"failed to run command: {' '.join(cmd)}", file=sys.stderr)
        proc = None

    # Ask user for which files to save by index.
    receiving_input = True
    while receiving_input:
        chosen = []
        save = input("Images to save? [default=1, a for all, c for cancel] ")
        receiving_input = False
        if save.strip().lower().startswith("a"):
            chosen = dups
        elif save.strip().lower().startswith("c"):
            print("could not complete deduplication", file=sys.stderr)
            if proc is not None:
                proc.terminate()
            sys.exit(1)
        elif len(save) == 0 or save.isspace():
            chosen.append(dups[0])
        else:
            for f in save.split(","):
                n = f.strip()
                if n.isdigit() and int(n) > 0 and int(n) <= len(dups):
                    chosen.append(dups[int(n) - 1])
                else:
                    # Input was not well formed.
                    receiving_input = True

    # Terminate image viewer.
    if proc is not None:
        proc.terminate()

    return chosen

# Identify duplicates to delete based on the paramaters set by the user.
def identify_to_delete(pairs, cli_args):
    # Initiate list of files to delete.
    to_delete = []

    # If the `pairs` option is on, use an algorithm to simply iterate through
    # the pairs.
    if cli_args.pairs:
        for pair in pairs:
            # Check if either file in the pair has already been marked for
            # deletion.
            if pair.file1 in to_delete or pair.file2 in to_delete:
                continue
            # Check if the pair's file2 should be automatically deleted.
            elif cli_args.auto_threshold is not None and pair.dist < cli_args.auto_threshold:
                to_delete.append(pair.file2)
                continue

            # Show viewer the images and ask which to save.
            to_save = choose_with_viewer([pair.file1, pair.file2], cli_args.viewer_command)
            if pair.file1 not in to_save:
                to_delete.append(pair.file1)
            if pair.file2 not in to_save:
                to_delete.append(pair.file2)

        return to_delete

    # Otherwise use the clustering algorithm.
    # Iterate through pairs of duplicates, display duplicates in feh, and allow
    # the user to choose which to save.
    # This works by iterating through a list of pairs, recording the first (oldest)
    # file in each pair, and then continuing to add files to a list of potential
    # duplicates until a pair with a different first file is reached.
    # This results in grouping duplicate files together instead of needing to
    # show individual pairs.
    i = 0
    while i < len(pairs):
        # Skip this pair if file1 is already marked for deletion.
        if pairs[i].file1 in to_delete:
            i += 1
            continue

        # Start list of dups with file1 in this pair, which should be the oldest
        # file in the cluster (these will be passed to `choose_with_viewer`).
        dups = [pairs[i].file1]

        # Continue iterating through pairs for as long as they share the same
        # first file.
        j = i
        while j < len(pairs) and pairs[j].file1 == pairs[i].file1:
            # Make sure file is not already scheduled for deletion.
            if pairs[j].file2 in to_delete:
                pass
            # Check if the auto-deletion threshold is met.
            elif cli_args.auto_threshold is not None and pairs[j].dist <= cli_args.auto_threshold:
                to_delete.append(pairs[j].file2)
            # Otherwise append file2 to list of dups.
            else:
                dups.append(pairs[j].file2)
            j += 1

        # Make sure that we are left with more than one image to show.
        if len(dups) <= 1:
            pass
        # Otherwise let the user choose what to save.
        else:
            # Mark all files not selected with feh for deletion.
            to_save = choose_with_viewer(dups, cli_args.viewer_command)
            to_delete.extend([d for d in dups if d not in to_save])

        # Update i to match j so the next iteration starts with a new file1.
        i = j

    return to_delete

# Delete the files provided, or print them without deleting if `dry_run` is True.
def delete_files(files, dry_run=False):
    print("About to delete:")
    print('\n'.join(files))
    ok = input("Ok to delete? [Y/n]")

    if ok.strip().lower().startswith("n"):
        return

    for d in files:
        if dry_run:
            print(d)
        else:
            try:
                os.remove(d)
            except:
                print(f"could not delete {d}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(
        prog="Image Duplicate Finder"
    )
    parser.add_argument("images", nargs="+")
    parser.add_argument("-l", "--list", default=False, action="store_true",
                        help="print list of pairs of duplicates sorted by distance instead of deleting")
    parser.add_argument("-p", "--pairs", default=False, action="store_true",
                        help="disable clustering and only show 1 pair of duplicates at a time")
    parser.add_argument("-c", "--viewer-command", default="feh -.", type=str,
                        help="command used to show potential duplicates (default feh -.)")
    parser.add_argument("-t", "--threshold", default=0.3, type=float, metavar="T",
                        help="the threshold at which to show potential duplicates for manual checking (default 0.3), or,"
                        "if used with the -l flag, the threshold at which to include potential duplicates in the output")
    parser.add_argument("-a", "--auto-threshold", default=None, type=float, metavar="A",
                        help="the threshold at which to automatically delete potential duplicates (default none)")
    parser.add_argument("-f", "--force", default=False, action="store_true",
                        help="disable file extension check before operating on files")
    parser.add_argument("-q", "--quiet", default=False, action="store_true")
    parser.add_argument("-d", "--dry-run", default=False, action="store_true",
                        help="print list of files to delete instead of actually deleting")

    args = parser.parse_args()

    # Get a list of images supplied by command-line arguments, as well as images
    # in supplied folders.
    files = []
    for file in args.images:
        if os.path.isfile(file):
            files.append(File(file))
        elif os.path.isdir(file):
            for r, d, f in os.walk(file):
                for name in f:
                    fp = os.path.join(r, name)
                    if name.split(".")[-1].lower() in image_exts or args.force:
                        files.append(File(fp))
                    elif not args.quiet:
                        print(f"{fp} is not a compatible image format, skipping...", file=sys.stderr)
        else:
            print(f"could not find {file}", file=sys.stderr)
            sys.exit(1)

    # Sort by last modification time so that older files are preferred.
    files.sort(key=lambda f: (f.last_modified, f.filepath))

    pairs = find_dups(files, args)

    if args.list:
        print_pairs(pairs)
    else:
        to_delete = identify_to_delete(pairs, args)
        delete_files(to_delete, args.dry_run)

if __name__ == "__main__":
    main()
