#!/bin/bash

CSV="/Volumes/RachnaPSSD/ConfigFiles/BUMP_with_NoLibraryGitHubURL.csv"
OUTPUT="/Volumes/RachnaPSSD/ConfigFiles/package_structure_summary.txt"

echo "Extracting base Java test package for all Docker images..." >> "$OUTPUT"

# Read CSV skipping header
tail -n +2 "$CSV" | while IFS=',' read -r custom_id clientURL clientProject clientOrg breakingCommit _; do
    for type in pre breaking; do
        IMAGE="ghcr.io/chains-project/breaking-updates:${breakingCommit}-${type}"
        echo -e "\n==== $custom_id | $type | $IMAGE ====" >> "$OUTPUT"

        docker run --rm --platform linux/amd64 "$IMAGE" sh -c '
            test_roots=$(find / -type d -path "*/src/test/java" 2>/dev/null | sort | uniq)

            if [ -z "$test_roots" ]; then
                echo "No src/test/java directories found."
                exit 0
            fi

            found_valid=false

            for test_root in $test_roots; do
                echo "Test root: $test_root"
                test_files=$(find "$test_root" -type f -name "*.java" | head -n 20)

                if [ -z "$test_files" ]; then
                    echo "No Java test files found in $test_root"
                    continue
                fi

                all_packages=$(for file in $test_files; do grep "^package " "$file" | head -n 1; done | sed "s/package //" | sed "s/;//" | sort | uniq)

                if [ -n "$all_packages" ]; then
                    base_package=$(echo "$all_packages" | awk -F. '\''{ print NF, $0 }'\'' | sort -n | head -n 1 | cut -d" " -f2-)
                    echo "package: ${base_package}"
                    echo "Detected packages:"
                    echo "$all_packages"
                    printf ":::${test_root}|package %s;:::\n" "$base_package"
                    found_valid=true
                    break
                fi
            done

            if [ "$found_valid" = false ]; then
                echo "Test directories found, but no valid Java files with package line."
            fi
        ' >> "$OUTPUT" 2>&1
    done
done

echo "Done. Results saved in $OUTPUT"