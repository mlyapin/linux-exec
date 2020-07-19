#!/usr/bin/env python3

import sys
import os
import subprocess
import hashlib
import typing
import dataclasses

DOCKERFILE_BASIC = """
FROM archlinux

# Update the system
RUN pacman --noconfirm -Syu

# We will append commands to install packages later. See the function `construct_dockerfile()`
"""


@dataclasses.dataclass
class Config:
    cmdprefix: str = "le-"
    memory: str = "1g"
    docker_cmd: str = "docker"
    packages: str = ""
    mounts: [str] = dataclasses.field(default_factory=list)
    cwd: str = dataclasses.field(default_factory=os.getcwd)
    tag: str = None
    repo: str = "linux-exec"

    def gen_tag(self):
        self.tag = calc_image_hash(self)

    def __post_init__(self):
        self.gen_tag()

    def get_repotag(self) -> str:
        return self.repo + ":" + self.tag

    @classmethod
    def from_env(cls):
        c = Config()
        c.cmdprefix = os.getenv("LE_PREFIX", c.cmdprefix)
        c.memory = os.getenv("LE_MEM", c.memory)
        c.docker_cmd = os.getenv("LE_DOCKERCMD", c.docker_cmd)
        c.packages = os.getenv("LE_PACKAGES", c.packages)
        c.cwd = os.getenv("LE_CWD", c.cwd)
        c.repo = os.getenv("LE_REPO", c.repo)

        mounts = os.getenv("LE_MOUNTS")
        if mounts is not None:
            c.mounts = mounts.split(":")

        c.gen_tag()
        return c


def calc_image_hash(config: Config) -> str:
    """Calculate image hash from given a config."""
    return hashlib.md5(config.packages.encode()).hexdigest()


def construct_dockerfile(config: Config) -> str:
    # We install packages one by one to reuse intermediate fs layers.
    def gen_install_commands(config: Config) -> typing.Iterator[str]:
        for package in config.packages.split(" "):
            if len(package) > 0:
                yield "RUN pacman --noconfirm -S {}".format(package)

    return os.linesep.join([DOCKERFILE_BASIC] + list(gen_install_commands(config)))


def docker_images(config: Config) -> typing.Iterator[str]:
    """Get all present docker images."""
    toexec = [config.docker_cmd, "image", "ls",
              "--format", "{{.Repository}}:{{.Tag}}"]
    p = subprocess.run(toexec, capture_output=True, text=True)
    p.check_returncode()

    for tag in p.stdout.splitlines():
        yield tag


def ensure_image_relevant(c: Config):
    if c.get_repotag() not in docker_images(c):
        print("Required image is missing. Building...")
        build_image(c)


def build_image(config: Config):
    toexec = [config.docker_cmd, "build",
              "-m", config.memory,
              "--label", "packages=" + config.packages,
              "-t", config.get_repotag(),
              # The dockerfile will be passed via stdin
              "-"]
    subprocess.run(toexec, encoding=sys.getdefaultencoding(),
                   input=construct_dockerfile(config))


def run_cmd(config: Config, cmd: str, args: [str]):
    ensure_image_relevant(config)

    toexec = [config.docker_cmd, "run",
              "-m", config.memory,
              "--rm",
              "--init",
              "-i",
              # Mount and change working directory.
              "-v", "".join([config.cwd, ":", config.cwd]),
              "-w", config.cwd]

    # Mount requested directories
    for d in config.mounts:
        if len(d) > 0:
            toexec += ["-v", "".join([d, ":", d])]

    # If stdin is piped from another process, do not allocate TTY.
    indata = None
    if sys.stdin.isatty():
        toexec += ["-t"]
    else:
        indata = sys.stdin.buffer.read()

    toexec += [config.get_repotag(), "sh", "-c",
               " ".join([cmd] + args)]

    subprocess.run(toexec, input=indata)


def main():
    config = Config.from_env()
    program = os.path.basename(sys.argv[0])
    args = sys.argv[1:]

    if not os.path.islink(sys.argv[0]):
        if len(args) == 0:
            # If the script was executed directly without arguments, build the image.
            build_image(config)
        else:
            # If arguments were passed, treat them as commands.
            run_cmd(config, args[0], args[1:])
    elif program[:len(config.cmdprefix)] == config.cmdprefix:
        # If the script was executed via link with a suffix, strip the prefix and run the command.
        run_cmd(config, program[len(config.cmdprefix):], args)
    else:
        # If the script was executed via link without a suffix, just run the command.
        run_cmd(config, program, args)


main()
