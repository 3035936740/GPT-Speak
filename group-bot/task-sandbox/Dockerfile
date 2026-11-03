FROM python:3.12-slim
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN printf '[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple\n' > /etc/pip.conf
RUN pip install --no-cache-dir mido==1.3.3 pillow==11.3.0 python-docx==1.2.0 openpyxl==3.1.5 pypdf==6.2.0
USER 65534:65534
WORKDIR /workspace
