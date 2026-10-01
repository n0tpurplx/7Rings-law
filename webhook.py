import json
from aiohttp import web


async def handle_erlc_webhook(
    request: web.Request
):
    db = request.app["database"]

    try:
        raw_body = await request.text()
    except Exception:
        return web.json_response(
            {
                "success": False,
                "error": "Could not read request body."
            },
            status=400
        )

    print("\n" + "=" * 60)
    print("ER:LC WEBHOOK RECEIVED")
    print("=" * 60)

    print(raw_body)

    print("=" * 60 + "\n")

    # Store the exact payload for debugging.
    db.save_webhook_event(raw_body)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return web.json_response(
            {
                "success": False,
                "error": "Invalid JSON."
            },
            status=400
        )

    # ---------------------------------------------------------
    # IMPORTANT:
    #
    # We intentionally do NOT assume the event schema yet.
    #
    # Once you send ;DP 10-8 in ER:LC, we'll see the exact
    # payload your server sends and build the parser around it.
    # ---------------------------------------------------------

    print("Parsed webhook payload:")
    print(json.dumps(
        payload,
        indent=2,
        ensure_ascii=False
    ))

    return web.json_response({
        "success": True
    })


async def healthcheck(
    request: web.Request
):
    return web.json_response({
        "status": "online",
        "service": "CAD Bot"
    })


def create_webhook_app(db):
    app = web.Application()

    app["database"] = db

    app.router.add_get(
        "/",
        healthcheck
    )

    app.router.add_get(
        "/health",
        healthcheck
    )

    app.router.add_post(
        "/erlc/webhook",
        handle_erlc_webhook
    )

    return app
