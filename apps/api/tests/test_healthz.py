from httpx import AsyncClient


async def test_healthz(client: AsyncClient):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
