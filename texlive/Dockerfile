# See here for image contents: https://github.com/microsoft/vscode-dev-containers/tree/v0.222.0/containers/ubuntu/.devcontainer/base.Dockerfile

# [Choice] Ubuntu version (use hirsuite or bionic on local arm64/Apple Silicon): hirsute, focal, bionic
ARG VARIANT="ubuntu"
FROM mcr.microsoft.com/devcontainers/base:${VARIANT}

# [Optional] Uncomment this section to install additional OS packages.
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive && \
    apt-get -y install --no-install-recommends texlive-xetex texlive-science texlive-lang-chinese texlive-lang-english texlive-bibtex-extra texlive-fonts-extra texlive-plain-generic texlive-pstricks texlive-latex-extra biber chktex latexmk ttf-mscorefonts-installer && \
    sudo git clone https://github.com/Haixing-Hu/latex-chinese-fonts /usr/share/fonts/latex-chinese-fonts && \
    apt upgrade -y && \
    apt-get clean all && \
    apt-get autoclean && \
    apt-get autoremove && \
    rm -rf /var/lib/apt/lists/*
