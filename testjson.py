from human_requests.abstraction import Output, URL

output = Output.from_raw(
        b"{bad json",
        headers={"content-type": "application/json"},
        url=URL("https://example.com/api"),
    )

output.json()
