Quick Start
===========

Установка
---------

Выберете один из вариантов отвечающий вашим требованиям:

.. code-block:: bash

    pip install network-manager[playwright]
    playwright install

Стандартный playwright с набором браузеров (Chrome, Firefox, WebKit).
Голым использовать не рекомендую.

.. code-block:: bash

    pip install network-manager[playwright-stealth]
    playwright install

Стандартный playwright со стелс-патчем, который скрывает некоторые сигнатуры автоматизированного браузера.

.. code-block:: bash

    pip install network-manager[camoufox]
    camoufox fetch

Playwright браузер на базе Firefox. Основная фишка - спуффинг сигнатур, что позволяет слать больше трафика и обходить баны по fingerprint.


Использование
-------------

.. code-block:: python

    from network_manager import Session, ImpersonationConfig, Policy, HttpMethod
    import asyncio
    import json

    async def main():
        # Инициализация сессии
        s = Session(headless=True, # False полезно для отладки
                    browser="camoufox", # camoufox самый лучший для массовых запросов, но он же порой менее стабильный
                    playwright_stealth=False, # в случае не camoufox (он уже поддержимвает это по умолчанию), скрывает некоторые сигнатуры автоматизированного браузера
                    spoof=ImpersonationConfig(
                        policy=Policy.INIT_RANDOM,
                        geo_country="RU",
                        sync_with_engine=False
                    ))

        # Прогрев сессии, в частности интересуют куки доступа + локальное хранилище с дефолтами
        async with s.goto_page("https://5ka.ru/", wait_until="networkidle") as page:
            await page.wait_for_selector(selector="next-route-announcer", state="attached")

        # Парсим текущий адрес магазина
        default_store_location = json.loads(s.local_storage["https://5ka.ru"]["DeliveryPanelStore"])

        # Куки подтянуться автоматически
        resp = await s.request(
            HttpMethod.GET, # Аналог "GET"
            # Вытягиваем из локального хранилище магазин по умолчанию
            f"https://5d.5ka.ru/api/catalog/v2/stores/{default_store_location['selectedAddress']['sapCode']}/categories?mode=delivery",
            headers={ # Статические заголовки, без них будет 400
                "X-PLATFORM": "webapp",
                # JS сайта записал при прогреве в локальное хранилище ID устройства
                "X-DEVICE-ID": s.local_storage["https://5ka.ru"]["deviceId"],
                "X-APP-VERSION": "0.1.1.dev"
            }
        )

        # Если во время парса ответа, вы обнаружили что сервер, например:
        # прислал JS челледж, который нужно выполнить, чтобы получить данные
        # вы можете отрендерить результат напрямую в браузере (без повтороного запроса)
        # плюс в том, что для сервера нет дублирующего реквеста (менее подозрительное поведение, экономия rate-limit'а)

        # async with resp.render() as p:
        #     await p.wait_for_load_state("networkidle")
        #     print(await p.content())

        # Не забываем закрыть сессию (в with контексте само бы закрылось)
        await s.close()
        
        # Проверяем результат
        assert resp.status_code == 200

        # Парсим тело
        json_result = json.loads(resp.body)

        # Дальше можем обрабатывать как хотим
        names = []
        for element in json_result:
            names.append(element["name"])

        from pprint import pprint
        pprint(names)

    if __name__ == "__main__":
        asyncio.run(main())
