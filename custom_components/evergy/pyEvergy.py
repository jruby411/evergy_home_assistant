"""Evergy Class Module."""

import json
import logging
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Final

import aiohttp

_LOGGER = logging.getLogger(__file__)

_DEBUG: bool = False

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"

DAY_INTERVAL: Final = "d"
HOUR_INTERVAL: Final = "h"
FIFTEEN_MINUTE_INTERVAL: Final = "mi"


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""

    def __init__(self, message):
        """Init."""
        super().__init__(message)


def get_past_date(days_back: int = 1) -> datetime:
    """Get a date based on a number of days back from today.

    :rtype: datetime
    :param days_back: The number of days back to get the date for
    :return: The date in the past
    """
    date_today = datetime.now(timezone.utc).date()
    dt_today = datetime(
            year = date_today.year,
            month = date_today.month,
            day = date_today.day,
        )
    return dt_today - timedelta(days=days_back)


def get_end_date_from_number_of_intervals(
        from_date: datetime,
        num_intervals: int = 1,
        interval: str = "d") -> datetime:
    """Get a date based on a number of intervals from a specific datetime.

    :rtype: datetime
    :param from_date: The start date and time
    :param num_intervals: the number of interval's at the given interval (inclusive)
    :param interval: 'd', 'h', or 'mi'
    :return: The datetime from the specified intervals
    """
    num_intervals -= 1
    if interval == "mi":
        return from_date + timedelta(minutes=15*num_intervals)
    if interval == "h":
        return from_date + timedelta(hours=num_intervals)
    return from_date + timedelta(days=num_intervals)


day_before_yesterday = get_past_date(2)
yesterday = get_past_date(1)
today = datetime.now(timezone.utc).today()


class EvergyException(Exception):
    """Evergy Exception Class."""

    def __init__(self, message):
        """Init."""
        super().__init__(message)


class EvergyDavinciWidgetParser(HTMLParser):
    """HTML parser to extract Davinci api and flow data for PingOne Authentication."""

    def __init__(self) -> None:
        """Initialize."""
        super().__init__()
        self.data: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Recognizes data-davinci attrs from davinci-widget-wrapper class."""
        if tag == "div" and ("class", "davinci-widget-wrapper") in attrs:
            _, token = next(filter(lambda attr: attr[0] == "data-davinci-company-id", attrs))
            self.data["company_id"] = str(token)
            _, token = next(filter(lambda attr: attr[0] == "data-davinci-sk-api-key", attrs))
            self.data["sk_api_key"] = str(token)
            _, token = next(filter(lambda attr: attr[0] == "data-davinci-api-root", attrs))
            self.data["api_root"] = str(token)
            _, token = next(filter(lambda attr: attr[0] == "data-davinci-policy-id", attrs))
            self.data["policy_id"] = str(token)
            _, token = next(filter(lambda attr: attr[0] == "data-davinci-post-processing-api", attrs))
            self.data["post_processing_api"] = str(token)
            _, token = next(filter(lambda attr: attr[0] == "data-davinci-datasource-item-id", attrs))
            self.data["datasource_item_id"] = str(token)


class EvergyLoginHandler:
    """Handle davinci widget authentication for Evergy Login page."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize."""
        self.session = session
        self.auth_data: dict[str, str]
        self.access_token: str
        self.connectionId: str
        self.interactionId: str
        self.flowId: str
        self.id: str

    async def get_auth_data(self) -> None:
        """Parse davinci widget for api data."""
        parse_auth_data = EvergyDavinciWidgetParser()

        login_page_url = "https://www.evergy.com/log-in"

        _LOGGER.debug("Fetching Evergy login page: %s", login_page_url)

        async with self.session.get(
            login_page_url,
            headers={"User-Agent": USER_AGENT},
            raise_for_status=True,
        ) as resp:
            parse_auth_data.feed(await resp.text())
            self.auth_data = parse_auth_data.data

            assert self.auth_data, "Failed to get davinci widget data"

    async def get_sdktoken(self) -> None:
        """First get the access_token."""
        login_sdktoken_url = (
            self.auth_data["api_root"].replace("auth", "orchestrate-api")
            + "/v1/company/"
            + self.auth_data["company_id"]
            + "/sdktoken"
        )

        _LOGGER.debug("Fetching Evergy login page: %s", login_sdktoken_url)

        async with self.session.get(
            login_sdktoken_url,
            headers={"User-Agent": USER_AGENT, "x-sk-api-key": self.auth_data["sk_api_key"]},
            raise_for_status=True,
        ) as resp:
            data = await resp.json()
            self.access_token = data["access_token"]

    async def start_flow(self) -> None:
        """Start the davinci widget flow."""
        login_start_url = (
            self.auth_data["api_root"]
            + "/"
            + self.auth_data["company_id"]
            + "/davinci/policy/"
            + self.auth_data["policy_id"]
            + "/start"
        )

        _LOGGER.debug("Fetching start page for davinci flow: %s", login_start_url)

        async with self.session.get(
            login_start_url,
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": "Bearer " + self.access_token,
            },
            raise_for_status=True,
        ) as resp:
            data = await resp.json()
            self.id = data["id"]
            self.connectionId = data["connectionId"]
            self.interactionId = data["interactionId"]
            self.flowId = data["flowId"]
            if _DEBUG:
                await Evergy.log_response(resp, self.session, "start_flow", "01")

    async def get_login_form(self) -> None:
        """Retrieve submit form."""
        login_template_url = (
            self.auth_data["api_root"]
            + "/"
            + self.auth_data["company_id"]
            + "/davinci/connections/"
            + self.connectionId
            + "/capabilities/customHTMLTemplate"
        )

        _LOGGER.debug("Fetching login template page: %s", login_template_url)

        async with self.session.post(
            login_template_url,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "interactionId": self.interactionId,
                "Origin": "https://www.evergy.com",
            },
            data=json.dumps(
                {
                    "id": self.id,
                    "eventName": "continue",
                }
            ),
            raise_for_status=True,
        ) as resp:
            data = await resp.json()
            self.id = data["id"]
            if _DEBUG:
                await Evergy.log_response(resp, self.session, "get_login_form", "02")

    async def submit_login_form(self, username: str, password: str) -> None:
        """Login to the utility website."""
        login_template_url = (
            self.auth_data["api_root"]
            + "/"
            + self.auth_data["company_id"]
            + "/davinci/connections/"
            + self.connectionId
            + "/capabilities/customHTMLTemplate"
        )

        _LOGGER.debug("Submit login data to template page: %s", login_template_url)

        async with self.session.post(
            login_template_url,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Origin": "https://www.evergy.com",
            },
            data=json.dumps(
                {
                    "id": self.id,
                    "nextEvent": {
                        "constructType": "skEvent",
                        "eventName": "continue",
                        "params": [],
                        "eventType": "post",
                        "postProcess": {},
                    },
                    "parameters": {
                        "buttonType": "form-submit",
                        "buttonValue": "submit",
                        "username": username,
                        "password": password,
                    },
                    "eventName": "continue",
                }
            ),
            allow_redirects=False,
            raise_for_status=True,
        ) as resp:
            data = await resp.json()
            """If the submitted login form returns a different flowId, then the username doesn't exist."""
            if data["flowId"] != self.flowId:
                raise InvalidAuth("No such username. Login failed.")
            """If the submitted login form returns the same id, then the password isn't correct."""
            if data["id"] == self.id:
                raise InvalidAuth("Wrong password. Login failed.")
            self.id = data["id"]
            if _DEBUG:
                await Evergy.log_response(resp, self.session, "submit_login_form", "03")

    async def get_new_connection_id(self) -> None:
        """Retrieve new connection id."""
        login_template_url = (
            self.auth_data["api_root"]
            + "/"
            + self.auth_data["company_id"]
            + "/davinci/connections/"
            + self.connectionId
            + "/capabilities/customHTMLTemplate"
        )

        _LOGGER.debug("Fetching login template page: %s", login_template_url)

        async with self.session.post(
            login_template_url,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Origin": "https://www.evergy.com",
            },
            data=json.dumps({"id": self.id, "eventName": "continue"}),
            raise_for_status=True,
        ) as resp:
            data = await resp.json()
            self.id = data["id"]
            self.connectionId = data["connectionId"]
            if _DEBUG:
                await Evergy.log_response(resp, self.session, "get_new_connection_id", "04")

    async def get_new_connection_cookie(self) -> None:
        """Set complete to generate cookie."""
        login_set_cookie_url = (
            self.auth_data["api_root"]
            + "/"
            + self.auth_data["company_id"]
            + "/davinci/connections/"
            + self.connectionId
            + "/capabilities/setCookieWithoutUser"
        )

        _LOGGER.debug("Start setCookieWithoutUser processing with new connectionId: %s", login_set_cookie_url)

        async with self.session.post(
            login_set_cookie_url,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "eventName": "complete",
                    "parameters": {},
                    "id": self.id,
                }
            ),
            raise_for_status=True,
        ) as resp:
            data = await resp.json()
            self.id = data["id"]
            if _DEBUG:
                await Evergy.log_response(resp, self.session, "get_new_connection_cookie", "05")

    async def get_new_access_token(self) -> None:
        """Set cookie and generate new access_token."""
        login_set_cookie_url = (
            self.auth_data["api_root"]
            + "/"
            + self.auth_data["company_id"]
            + "/davinci/connections/"
            + self.connectionId
            + "/capabilities/setCookieWithoutUser"
        )

        _LOGGER.debug("Fetch new access_token with new connectionId: %s", login_set_cookie_url)

        async with self.session.post(
            login_set_cookie_url,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "eventName": "complete",
                    "parameters": {},
                    "id": self.id,
                }
            ),
            raise_for_status=True,
        ) as resp:
            data = await resp.json()
            self.id = data["id"]
            self.access_token = data["access_token"]
            if _DEBUG:
                await Evergy.log_response(resp, self.session, "get_new_access_token", "06")

    async def postprocessing_api(self) -> None:
        """Postprocess url to get access by cookie."""
        login_postprocess_url = "https://www.evergy.com" + self.auth_data["post_processing_api"]

        _LOGGER.debug("Set cookie with new token for login access: %s", login_postprocess_url)

        async with self.session.post(
            login_postprocess_url,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            data=json.dumps({"Token": self.access_token, "DataSourceItemId": self.auth_data["datasource_item_id"]}),
            raise_for_status=True,
        ) as resp:
            await resp.json(content_type=None)
            if _DEBUG:
                await Evergy.log_response(resp, self.session, "postprocessing_api", "07")

    async def login(self, username: str, password: str) -> None:
        """First parse davinci widget for api data."""
        await EvergyLoginHandler.get_auth_data(self)
        """Get the access_token."""
        await EvergyLoginHandler.get_sdktoken(self)
        """Start the flow."""
        await EvergyLoginHandler.start_flow(self)
        """Retrieve submit form."""
        await EvergyLoginHandler.get_login_form(self)
        """Submit login form."""
        await EvergyLoginHandler.submit_login_form(self, username, password)
        """Retrieve new connection id."""
        await EvergyLoginHandler.get_new_connection_id(self)
        """Set complete to generate cookie."""
        await EvergyLoginHandler.get_new_connection_cookie(self)
        """Set cookie and generate new access_token."""
        await EvergyLoginHandler.get_new_access_token(self)
        """Postprocess url at Evergy to get access by cookie."""
        await EvergyLoginHandler.postprocessing_api(self)


class EvergyLogoutHandler:
    """Handle Evergy Logout and close session."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize."""
        self.session = session

    async def logout(self) -> None:
        """Logout."""
        logout_page_url = "https://www.evergy.com/logout"

        _LOGGER.debug("Logging out of Evergy: %s", logout_page_url)

        async with self.session.get(
            logout_page_url,
            headers={"User-Agent": USER_AGENT},
            raise_for_status=True,
        ) as resp:
            text = await resp.text()

            assert text, "Failed to logout."

        await self.session.close()


class Evergy:
    """Evergy class."""

    def __init__(self, username: str, password: str) -> None:
        """Initialize."""
        self.logged_in: bool = False
        self.session: aiohttp.ClientSession
        self.username: str = username
        self.password: str = password
        self.usage_data: dict[str, Any] | None = None
        self.dashboard_data: dict[str, Any] | None = None
        self.account_number: str | None = None
        self.premise_id: str | None = None
        self.account_summary_url: str = (
            "https://www.evergy.com/sc-api/account/getaccountpremiseselector?isWidgetPage=false&hasNoSelector=false"
        )
        self.account_dashboard_url: str = (
            "https://www.evergy.com/api/account/{accountNum}/dashboard/current"
        )
        self.usageDataUrl: str = (
            "https://www.evergy.com/api/report/usage/{premise_id}?interval={interval}&from={start}&to={end}"
        )

    async def login(self) -> bool:
        """Login to Evergy.

        The Evergy Login now has dynamic content. To avoind using something like
        requests_html or selenium I decided to build the dynamic request form the
        hard way. It has been a learning experience.
        """
        self.session = aiohttp.ClientSession()

        """Evergy log-in flow with davinci widget."""
        login_evergy = EvergyLoginHandler(self.session)
        await login_evergy.login(self.username, self.password)

        async with self.session.get(
            self.account_summary_url,
            headers={"User-Agent": USER_AGENT},
            raise_for_status=True,
        ) as response:
            account_data = await response.json(content_type=None)
            assert account_data, "Failed to get Evergy account data"
            if len(account_data) == 0:
                self.logged_in = False
            else:
                # shape is: [{"accountNumber": 123456789, "oPowerDomain": "kcpl.opower.com", ...}]
                self.account_number = account_data[0]["accountNumber"]
                async with self.session.get(
                    self.account_dashboard_url.format(accountNum=self.account_number),
                    headers={"User-Agent": USER_AGENT},
                    raise_for_status=True,
                ) as resp:
                    self.dashboard_data = await resp.json(content_type=None)

                self.premise_id = self.dashboard_data["addresses"][0]["premiseId"]
                self.logged_in = (
                    self.account_number is not None and self.premise_id is not None
                )
        if self.logged_in:
            _LOGGER.info("Logged in as: %s, on account: %s", self.username, self.account_number)
        return self.logged_in

    async def logout(self):
        """Log out of Evergy Portal."""
        _LOGGER.info("Logging out: %s", self.username)

        logout_evergy = EvergyLogoutHandler(self.session)
        await logout_evergy.logout()
        self.logged_in = False

    async def get_usage(self,
                        days: int = 1,
                        interval: str = DAY_INTERVAL) -> dict[str, Any] | None:
        """Get the energy usage data for today.

        Useful for getting the most recent data.

        :rtype: [dict]
        :param days: The number of back to get data for.
        :param interval: The time period between each data element in the returned data. Default is days.
        :return: A list of usage elements. The number of elements will depend on the `interval` argument.
        """
        start = get_past_date(days_back=(days - 1))
        end = get_past_date(0)
        return await self.get_usage_range(start, end, interval=interval)

    async def get_usage_from(self,
                             start: datetime | None = None,
                             size: int = 1,
                             interval: str = DAY_INTERVAL) -> dict[str, Any] | None:
        """Get a range of historical usage by providing only a start and number from.

        :param start: The date to begin getting data for (inclusive)
        :param size: The number of intervals of data to retrieve. (inclusive)
        :param interval: The time period between each data element in the returned data.
                         Default is days.
        :return: A list of usage elements. The number of elements will depend on the
                 `interval` argument.
        """
        if start is None:
            start = get_end_date_from_number_of_intervals(datetime.now())
        end = get_end_date_from_number_of_intervals(start, size, interval)
        return await self.get_usage_range(start, end, interval=interval)

    async def get_usage_range(self,
                              start: datetime | None = None,
                              end: datetime | None = None,
                              interval: str = DAY_INTERVAL) -> dict[str, Any] | None:
        """Get a specific range of historical usage. Could be useful for reporting.

        :param start: The date to begin getting data for (inclusive)
        :param end: The last date to get data for (inclusive)
        :param interval: The time period between each data element in the returned data. Default is days.
        :return: A list of usage elements. The number of elements will depend on the `interval` argument.
        """
        if start is None:
            start = get_past_date(0)
        if end is None:
            end = get_past_date(0)
        if not self.logged_in:
            await self.login()
        if start > end:
            msg = "'start' date can't be after 'end' date"
            _LOGGER.exception("%s",msg)
            raise EvergyException(msg)

        url = self.usageDataUrl.format(
            premise_id=self.premise_id,
            interval=interval,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        _LOGGER.info("Fetching %s", url)
        async with self.session.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            raise_for_status=True,
        ) as resp:
            usage_response = await resp.json()

        # all errors handled above.
        if usage_response is None:
            self.usage_data = None
            return None
        self.usage_data = usage_response["data"]
        return {"usage": self.usage_data , "dashboard": self.dashboard_data}

    @staticmethod
    async def log_response(
        response: aiohttp.ClientResponse,
        session: aiohttp.ClientSession,
        note: str | None = None,
        prefix: str | None = None,
    ) -> None:
        """Log any redirects and new cookies. Log full JSON when -vv is set."""
        _LOGGER.debug("Response from: %s", response.url)

        """This is just a stub so I can use the same code from evergy.py in pyEvergy.py"""
