FROM crpi-wh1i56a4x558rrhm.cn-hangzhou.personal.cr.aliyuncs.com/changqinga/knowledge-retrieval:v1.2

COPY docker/fonts/wqy-microhei.ttc /usr/share/fonts/truetype/wqy/wqy-microhei.ttc

RUN python -m pip install --no-cache-dir \
    Markdown==3.6 \
    weasyprint==61.2 \
    pydyf==0.8.0 \
    && fc-cache -f

COPY backend /app
COPY shared /app/shared
COPY services/rag/core /workspace/rag/core

WORKDIR /app
