FROM node:16.15.1-bullseye-slim AS assets

WORKDIR /app/assets

ARG UID=1000
ARG GID=1000

RUN apt-get update \
    && apt-get install -y build-essential \
    && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man \
    && apt-get clean \
    && groupmod -g "${GID}" node && usermod -u "${UID}" -g "${GID}" node \
    && mkdir -p /node_modules && chown node:node -R /node_modules /app

USER node

COPY --chown=node:node assets/package.json assets/*yarn* ./

RUN yarn install && yarn cache clean

ARG NODE_ENV="production"
ENV NODE_ENV="${NODE_ENV}" \
    PATH="${PATH}:/node_modules/.bin" \
    USER="node"

COPY --chown=node:node . ..

RUN if [ "${NODE_ENV}" != "development" ]; then \
    ../run yarn:build:js && ../run yarn:build:css; else mkdir -p /app/public; fi

CMD ["bash"]

###############################################################################

FROM --platform=linux/amd64 python:3.10.5-slim-bullseye AS app

WORKDIR /app

RUN sed -i -e's/ main/ main contrib non-free archive stretch /g' /etc/apt/sources.list
RUN apt-get update && apt-get install -y build-essential curl libpq-dev python3-dev default-libmysqlclient-dev aria2 unrar unzip p7zip curl python3 python3-pip ctorrent mariadb-client pv rclone gcc g++ make wget git cmake ca-certificates curl gnupg sshpass p7zip-full p7zip-rar libatomic1 libglib2.0-0 pigz parallel shellcheck jq

# https://github.com/nodesource/distributions
RUN mkdir -p /etc/apt/keyrings
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
ENV NODE_MAJOR=20
RUN echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list
RUN apt-get update && apt-get install nodejs -y
RUN npm install webtorrent-cli -g && webtorrent --version

# Install latest, with support for threading for t2sz
RUN git clone --depth 1 https://github.com/facebook/zstd --branch v1.5.6
RUN cd zstd && make && make install
# Install t2sz
RUN git clone --depth 1 https://github.com/martinellimarco/t2sz --branch v1.1.2
RUN mkdir t2sz/build
RUN cd t2sz/build && cmake .. -DCMAKE_BUILD_TYPE="Release" && make && make install
# Env for t2sz finding latest libzstd
ENV LD_LIBRARY_PATH=/usr/local/lib

RUN npm install elasticdump@6.112.0 -g

RUN wget https://github.com/mydumper/mydumper/releases/download/v0.17.1-1/mydumper_0.17.1-1.bullseye_amd64.deb
RUN dpkg -i mydumper_*.deb

RUN rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man
RUN apt-get clean

COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/venv
ENV PATH="/venv/bin:/root/.local/bin:$PATH"

# Changing the default UV_LINK_MODE silences warnings about not being able to use hard links since the cache and sync target are on separate file systems.
ENV UV_LINK_MODE=copy
# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project
# Install playwright's dependencies
RUN --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=tmpfs,target=/usr/share/doc \
    --mount=type=tmpfs,target=/usr/share/man \
    playwright install chromium --with-deps

# Download models
RUN echo 'import fast_langdetect; fast_langdetect.detect("dummy")' | python3
# RUN echo 'import sentence_transformers; sentence_transformers.SentenceTransformer("intfloat/multilingual-e5-small")' | python3

ARG FLASK_DEBUG="false"
ENV FLASK_DEBUG="${FLASK_DEBUG}" \
    FLASK_APP="allthethings.app" \
    FLASK_SKIP_DOTENV="true" \
    PYTHONUNBUFFERED="true" \
    PYTHONPATH="."

ENV PYTHONFAULTHANDLER=1

# Get pdf.js
RUN mkdir -p /public
RUN wget https://github.com/mozilla/pdf.js/releases/download/v4.5.136/pdfjs-4.5.136-dist.zip -O /public/pdfjs-4.5.136-dist.zip
RUN rm -rf /public/pdfjs
RUN mkdir /public/pdfjs
RUN unzip /public/pdfjs-4.5.136-dist.zip -d /public/pdfjs
# Remove lines
RUN sed -i -e '/if (fileOrigin !== viewerOrigin) {/,+2d' /public/pdfjs/web/viewer.mjs

# Get foliate.js
RUN git clone --depth 1 https://github.com/johnfactotum/foliate-js /public/foliatejs \
    && cd /public/foliatejs \
    && git fetch origin 34b9079a1b7a325febfb3728f632e636d402a372 --depth 1 \
    && git checkout 34b9079a1b7a325febfb3728f632e636d402a372 \
    && rm -rf /public/foliatejs/.git
# Monkey patch fetchFile (needed, as important metadata is lost when calling createObjectURL)
RUN sed -i 's/await fetchFile(file)/await window.parent.fetchFile(file)/g' /public/foliatejs/view.js
# Monkey patch onLoad to automatically refocus the iframe
RUN sed -i '/#onLoad({ detail: { doc } }) {/!b;n;a\\t\twindow.top.postMessage("refocus-iframe");' /public/foliatejs/reader.js

# Get djvu.js
RUN curl -L https://github.com/RussCoder/djvujs/releases/download/L.0.5.4_V.0.10.1/djvu.js --create-dirs -o /public/djvujs/djvu.js 
RUN curl -L https://github.com/RussCoder/djvujs/releases/download/L.0.5.4_V.0.10.1/djvu_viewer.js --create-dirs -o /public/djvujs/djvu_viewer.js 

# Get kthoom
RUN git clone --depth 1 https://github.com/codedread/kthoom /public/kthoom \
    && cd /public/kthoom \
    && git fetch origin 6ec1a413f26c42957c527879e75d03a705a3a8df --depth 1 \
    && git checkout 6ec1a413f26c42957c527879e75d03a705a3a8df \
    && rm -rf /public/kthoom/.git

# Get villain.js 
RUN curl -L https://raw.githubusercontent.com/btzr-io/Villain/refs/heads/master/packages/villain-react/dist/villain.js --create-dirs -o /public/villainjs/villain.js
RUN curl -L https://raw.githubusercontent.com/btzr-io/Villain/refs/heads/master/packages/villain-react/dist/style.css --create-dirs -o /public/villainjs/style.css
# Get libarchive.js (villain.js dependancy)
RUN wget https://github.com/nika-begiashvili/libarchivejs/archive/refs/tags/v1.3.0.zip -O /public/libarchive-v1.3.0.zip
RUN rm -rf /public/libarchivejs
RUN mkdir /public/libarchivejs
RUN unzip /public/libarchive-v1.3.0.zip -d /public/libarchivejs

COPY --from=assets /app/public /public

COPY . .

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# RUN if [ "${FLASK_DEBUG}" != "true" ]; then \
#   ln -s /public /app/public && flask digest compile && rm -rf /app/public; fi

ENTRYPOINT ["/app/bin/docker-entrypoint-web"]

EXPOSE 8000

CMD ["gunicorn", "-c", "python:config.gunicorn", "allthethings.app:create_app()"]
