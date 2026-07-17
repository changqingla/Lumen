FROM crpi-wh1i56a4x558rrhm.cn-hangzhou.personal.cr.aliyuncs.com/changqinga/knowledge-retrieval:v1.2

ENV VIRTUAL_ENV=/opt/lumen-backend-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

COPY docker/fonts/wqy-microhei.ttc /usr/share/fonts/truetype/wqy/wqy-microhei.ttc
COPY backend/requirements.txt /tmp/backend-requirements.txt

RUN python -m venv "${VIRTUAL_ENV}" \
    && python -m pip install --no-cache-dir -r /tmp/backend-requirements.txt \
    && python -m pip check \
    && rm -f /tmp/backend-requirements.txt \
    && fc-cache -f

COPY docker/backend-venv.sh /etc/profile.d/lumen-backend-venv.sh

COPY backend /app
COPY shared /app/shared
COPY services/rag/core /workspace/rag/core

WORKDIR /app
