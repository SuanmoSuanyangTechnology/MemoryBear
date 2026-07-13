#!/bin/bash

# Check if the correct number of arguments are provided
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <src> <dest>"
    exit 1
fi

src="$1"
dest="$2"

# Function to copy and link files
copy_and_link() {
    local src_file="$1"
    local dest_file="$2"

    if [ -L "$src_file" ]; then
        # If src_file is a symbolic link, copy it without changing permissions
        cp -P "$src_file" "$dest_file"
    elif [ -b "$src_file" ] || [ -c "$src_file" ]; then
        # If src_file is a device file, copy it and change permissions
        cp "$src_file" "$dest_file"
        chmod 444 "$dest_file"
    else
        # Otherwise, create a hard link and change the permissions to read-only
        ln -f "$src_file" "$dest_file" 2>/dev/null || { cp "$src_file" "$dest_file" && chmod 444 "$dest_file"; }
    fi
}

# Check if src is a file or directory
if [ -f "$src" ]; then
    # src is a file, create hard link directly in dest
    mkdir -p "$(dirname "$dest/$src")"
    copy_and_link "$src" "$dest/$src"
elif [ -d "$src" ]; then
    # src is a directory, process as before
    mkdir -p "$dest/$src"

    # Find all files in the source directory
    find "$src" -type f,l | while read -r file; do
        # Get the relative path of the file
        rel_path="${file#$src/}"
        # Get the directory of the relative path
        rel_dir=$(dirname "$rel_path")
        # Create the same directory structure in the destination
        mkdir -p "$dest/$src/$rel_dir"
        # Copy and link the file
        copy_and_link "$file" "$dest/$src/$rel_path"
    done
else
    echo "Error: $src is neither a file nor a directory"
    exit 1
fi
