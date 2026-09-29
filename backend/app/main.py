from fastapi import FastAPI

from app import auth,onboarding,company,pieces

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="TVA App")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(company.router)
app.include_router(pieces.router)