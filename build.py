import os
import platform
import shutil
from distutils.command.build_ext import build_ext
from distutils.core import Distribution, Extension
from pathlib import Path

from Cython.Build import cythonize

compile_args_x86 = ["-march=native", "-O3", "-msse", "-msse2", "-mfma", "-mfpmath=sse"]
compile_args_arm = ["-O3"]
link_args = []
include_dirs = []
libraries = ["m"]


def build():
    cython_sources = []
    cython_sources.extend(Path("aiotone").glob("*.pyx"))

    cpu_arch = platform.machine().lower()
    if "arm" in cpu_arch:
        compile_args = compile_args_arm
    elif "x86" in cpu_arch or "amd64" in cpu_arch:
        compile_args = compile_args_x86
    else:
        print(f"warning: unknown machine arch {cpu_arch}; assuming Intel")
        compile_args = compile_args_x86

    extensions = [
        Extension(
            "aiotone." + path.with_suffix("").name,
            [str(path)],
            extra_compile_args=compile_args,
            extra_link_args=link_args,
            include_dirs=include_dirs,
            libraries=libraries,
        )
        for path in cython_sources
    ]
    ext_modules = cythonize(
        extensions,
        include_path=include_dirs,
        compiler_directives={"binding": True, "language_level": 3},
    )

    distribution = Distribution({"name": "extended", "ext_modules": ext_modules})
    distribution.package_dir = "extended"

    cmd = build_ext(distribution)
    cmd.ensure_finalized()
    cmd.run()

    # Copy built extensions back to the project
    for output in cmd.get_outputs():
        relative_extension = os.path.relpath(output, cmd.build_lib)
        shutil.copyfile(output, relative_extension)
        mode = os.stat(relative_extension).st_mode
        mode |= (mode & 0o444) >> 2
        os.chmod(relative_extension, mode)


if __name__ == "__main__":
    build()
