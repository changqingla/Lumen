ARG BASE_SANDBOX_IMAGE=crpi-wh1i56a4x558rrhm.cn-hangzhou.personal.cr.aliyuncs.com/changqinga/sandbox:latest
FROM ${BASE_SANDBOX_IMAGE}

COPY runtimes/skills/public/ppt-master/requirements.txt /tmp/ppt-master-requirements.txt

RUN python3 -m pip install --no-cache-dir --ignore-installed "blinker>=1.9.0" \
    && python3 -m pip install --no-cache-dir -r /tmp/ppt-master-requirements.txt \
    && python3 -m pip install --no-cache-dir "tinycss2>=1.5.0" \
    && rm -f /tmp/ppt-master-requirements.txt
