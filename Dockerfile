# The website for hosting (Render, Railway, Fly, Cloud Run): the landing page at /, email sign-in, Brodie's line (free / busy /
# offline) and the lesson list. Lessons are FaceTime only: Brodie's worker (the coach itself) runs elsewhere on the Chert line.
FROM python:3.12-slim
WORKDIR /srv
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt
COPY brodie/ brodie/
COPY web/ web/
COPY starter/dance/__init__.py starter/dance/keypoints.py starter/dance/reference.py starter/dance/
COPY data/dances/*.npz data/dances/
ENV HOST=0.0.0.0 PORT=8765
EXPOSE 8765
CMD ["python", "-m", "brodie.server"]
