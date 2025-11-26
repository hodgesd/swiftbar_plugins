@bootstrap:
    python -m pip install --upgrade pip uv
    python -m uv pip install --upgrade --requirement requirements.in

@lock *ARGS:
    python -m uv pip compile {{ ARGS }} ./requirements.in \
        --resolver=backtracking \
        --output-file ./requirements.txt