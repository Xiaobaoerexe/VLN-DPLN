"""Keep one user README; preserve third-party legal text as plain text."""
import argparse
import io
from pathlib import Path
import sys
import tarfile

START_README = '''# 启动平台

打开终端，运行：

```bash
cd ~/Documents/DroneNavTeaching
bash start.sh
```
'''


def prune_markdown(root):
    root = Path(root).resolve()
    removed = preserved = 0
    for path in root.rglob('*'):
        if path.suffix.lower() not in ('.md', '.markdown') or not (path.is_file() or path.is_symlink()):
            continue
        if path == root / 'README.md':
            continue
        # Third-party notices remain available with their original contents.
        if any(word in path.stem.lower() for word in ('license', 'licence', 'copying', 'notice')):
            destination = path.with_name(path.name + '.txt')
            if destination.exists() and destination.read_bytes() != path.read_bytes():
                raise RuntimeError('Conflicting legal-text destination: ' + str(destination))
            destination.write_bytes(path.read_bytes())
            preserved += 1
        path.unlink()
        removed += 1
    (root / 'README.md').write_text(START_README, encoding='utf-8')
    return dict(markdown_removed=removed, legal_text_preserved=preserved)


def filter_archive():
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as source:
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as target:
            for member in source:
                path = Path(member.name)
                if member.name == 'README.md':
                    continue
                if path.suffix.lower() in ('.md', '.markdown'):
                    if not any(word in path.stem.lower() for word in ('license', 'licence', 'copying', 'notice')):
                        continue
                    if not member.isfile():
                        raise RuntimeError('Legal Markdown resource must be a regular file: ' + member.name)
                    member.name += '.txt'
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
            data = START_README.encode('utf-8')
            info = tarfile.TarInfo('README.md')
            info.size, info.mode = len(data), 0o644
            target.addfile(info, io.BytesIO(data))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?')
    parser.add_argument('--filter-tar', action='store_true')
    args = parser.parse_args()
    if args.filter_tar:
        filter_archive()
    elif args.root:
        print(prune_markdown(args.root))
    else:
        parser.error('Provide the installation root or --filter-tar')
