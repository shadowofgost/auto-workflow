"""Resolve one real package version available for both supported architectures."""
from functools import cmp_to_key
import gzip
import subprocess


def versions(text, architecture):
    found = set()
    for paragraph in text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Package") == "cloudflare-warp" and fields.get("Architecture") == architecture:
            found.add(fields["Version"])
    return found


def main():
    available = []
    for architecture in ("amd64", "arm64"):
        url = f"https://pkg.cloudflareclient.com/dists/resolute/main/binary-{architecture}/Packages.gz"
        metadata = subprocess.check_output(["curl", "-fsSL", "--retry", "2", "--max-time", "30", url])
        available.append(versions(gzip.decompress(metadata).decode(), architecture))
    common = available[0] & available[1]
    if not common:
        raise SystemExit("No common amd64/arm64 WARP version in the resolute repository")

    def compare(left, right):
        return -1 if subprocess.run(["dpkg", "--compare-versions", left, "lt", right]).returncode == 0 else 1

    print(sorted(common, key=cmp_to_key(compare))[-1])


if __name__ == "__main__":
    main()
