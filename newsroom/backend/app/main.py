from fastapi import FastAPI


app = FastAPI(title="newsroom")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
