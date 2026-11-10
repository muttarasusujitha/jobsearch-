import os

import uvicorn

uvicorn.run("services.api.main:app", host=os.environ.get("API_HOST", "0.0.0.0"), port=int(os.environ.get("API_PORT", "8000")))
