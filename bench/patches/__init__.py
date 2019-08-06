import difflib
import importlib
from pathlib import Path


def run(bench_path):
	# build file paths
	current_dir = Path(__file__).parent
	patch_dir = Path(bench_path).resolve()

	source_file = current_dir / "patches.txt"
	target_file = patch_dir / "patches.txt"

	# get already executed patches
	if not target_file.exists():
		executed_patches = []
	else:
		executed_patches = list(filter(None, target.read_text().splitlines()))

	# get the new bench patches that need to be run
	with open(str(source_file), 'r') as source:
		with open(str(target_file), 'r') as target:
			diff = difflib.unified_diff(source.readlines(), target.readlines(), n=0)

			try:
				for line in diff:
					# ignore diff descriptions, skipped patches and empty lines
					for prefix in ('---', '+++', '@@', '-#'):
						if line.startswith(prefix) or not line[1:].strip():
							break
					else:
						patch = line[1:].strip().split()[0]
						module = importlib.import_module(patch)
						execute = getattr(module, 'execute')
						result = execute(bench_path)

						if result is not False:
							executed_patches.append(patch)
			finally:
				with open(str(target_file), 'w') as target:
					target.write('\n'.join(executed_patches))
					target.write('\n')  # end with an empty line


def set_all_patches_executed(bench_path):
	current_dir = Path(__file__).parent
	patch_dir = Path(bench_path).resolve()

	source_file = current_dir / "patches.txt"
	target_file = patch_dir / "patches.txt"

	with open(str(source_file), 'r') as source:
		with open(str(target_file), 'w') as target:
			target.write(source.read())
