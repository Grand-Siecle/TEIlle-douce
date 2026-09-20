# -----------------------------------------------------------
# What one modernization request looks like on the wire, and how big it is.
#
# VieuxParler sizes its own model batches from a token budget since its
# batching release; the `batch_size` field of /translate/batch is kept
# there for compatibility and sizes nothing — except `1`, which selects
# the line-by-line reference path, a tenth of the throughput on a GPU.
# A client that keeps sending the field can only lose by it: the value
# picks nothing, and a `--batch-size 1` would silently pick the slow
# path. So the request carries the lines and nothing else.
#
# Run: venv/bin/python -m pytest tests/test_modernize_request.py -q
# -----------------------------------------------------------
import asyncio
import json

import httpx

import teille_douce.modernize as modernize


def _post_through(handler, texts):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await modernize._send_batch(client, "http://vieuxparler", texts)
    return asyncio.run(go())


def test_the_request_carries_the_lines_and_no_batch_size():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"translations": ["une ligne"],
                                         "processing_time": 0.0, "count": 1})

    assert _post_through(handler, ["une ligne"]) == ["une ligne"]
    assert seen == [{"texts": ["une ligne"]}]

