"""Simple hello-world FastAPI app for integration testing the MCP feature."""
from pydantic import BaseModel

from fastapi import FastAPI

app = FastAPI(mcp_url="/mcp")


@app.get("/", summary="Root")
async def root() -> dict[str, str]:
    return {"message": "Hello World"}


@app.get("/hello/{name}", summary="Greet by name")
async def hello(name: str) -> dict[str, str]:
    return {"message": f"Hello, {name}!"}


class Item(BaseModel):
    name: str
    price: float


@app.post("/items", summary="Create an item")
async def create_item(item: Item) -> Item:
    return item


@app.get("/items/{item_id}", summary="Get an item by ID")
async def get_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}
