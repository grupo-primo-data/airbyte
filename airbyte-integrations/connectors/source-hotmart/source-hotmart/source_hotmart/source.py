#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#


from abc import ABC
from typing import Any, Iterable, List, Mapping, MutableMapping, Optional, Tuple
from datetime import datetime, timezone, timedelta

import requests
from airbyte_cdk.sources import AbstractSource
from airbyte_cdk.sources.streams import Stream
from airbyte_cdk.sources.streams.http import HttpStream
from airbyte_cdk.sources.streams.http.auth import TokenAuthenticator, HttpAuthenticator

def timestamp_to_unix_epoch(timestamp):
    dt_object = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    dt_object = dt_object.replace(tzinfo=timezone.utc)
    millisec = dt_object.timestamp() * 1000
    return int(millisec)


class HotmartTokenAuthenticator(HttpAuthenticator):
    def __init__(self, token_endpoint: str, client_id: str, client_secret: str, basic_token: str):
        self.token_endpoint = token_endpoint
        self.client_id = client_id
        self.client_secret = client_secret
        self.basic_token = basic_token
        self._token = None
        self._token_expires = datetime.now()

    def get_auth_header(self) -> Mapping[str, Any]:
        if not self._token or self._token_expires <= datetime.now() + timedelta(minutes=1):
            # Fetch a new token if the current one is not set or is about to expire
            self._fetch_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _fetch_token(self):
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Basic {self.basic_token}"
        }
        params = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        response = requests.request(method="POST", url=self.token_endpoint, headers=headers, params=params)
        response.raise_for_status()  # ensure we throw if an error happens
        token_data = response.json()
        self._token = token_data["access_token"]
        self._token_expires = datetime.now() + timedelta(seconds=token_data["expires_in"])


class HotmartStream(HttpStream, ABC):

    url_base = "https://developers.hotmart.com/payments/api/v1/"
    page_size = 500

    def __init__(self, authenticator: HotmartTokenAuthenticator, config: Mapping[str, Any], **kwargs):

        super().__init__(authenticator=authenticator)
        self.config = config


    def next_page_token(self, response: requests.Response) -> Optional[Mapping[str, Any]]:

        page_info = response.json()['page_info']

        if 'next_page_token' in page_info:
            return {"page_token": page_info['next_page_token']}
        else:
            return None

    def parse_response(self, response: requests.Response, **kwargs) -> Iterable[Mapping]:

        for r in response.json()["items"]:
            r['emitted_at'] = int(datetime.now(timezone.utc).timestamp()*1000)
            yield r


# Basic incremental stream
class IncrementalHotmartStream(HotmartStream, ABC):

    state_checkpoint_interval = 1000

    def get_updated_state(self, current_stream_state: MutableMapping[str, Any], latest_record: Mapping[str, Any]) -> Mapping[str, Any]:

        latest_record_date = latest_record[self.cursor_field]
        latest_record_state = {self.cursor_field: latest_record[self.cursor_field]}
        current_record_date = current_stream_state.get(self.cursor_field)

        if latest_record_date and current_record_date:
            if latest_record_date > current_record_date:
                return latest_record_state
            else:
                return current_stream_state
        if latest_record_date:
            return latest_record_state
        if current_record_date:
            return current_stream_state
        else:
            return {}


class SalesHistory(IncrementalHotmartStream):

    cursor_field = "order_date"
    primary_key = ["purchase","transaction"]

    def path(self, **kwargs) -> str:
        
        return "sales/history"

    def request_params(
        self, stream_state: Mapping[str, Any], stream_slice: Mapping[str, any] = None, next_page_token: Mapping[str, Any] = None
    ) -> MutableMapping[str, Any]:
 
        params = super().request_params(stream_state=stream_state)
        cursor_value = stream_state.get(self.cursor_field) or timestamp_to_unix_epoch(self.config['start_date'])

        params = {
            "max_results": self.page_size,
            "transaction_status": self.config['transaction_status'],
            "start_date": cursor_value,
            "end_date": int(datetime.now(timezone.utc).timestamp()*1000)
        }

        if next_page_token:
            params.update(next_page_token)

        return params

    def parse_response(self, response: requests.Response, **kwargs) -> Iterable[Mapping]:

        data = response.json()["items"]
        for d in data:
            d['order_date'] = d['purchase']['order_date']
            yield d

class SalesPriceDetails(IncrementalHotmartStream):

    cursor_field = "emitted_at"
    primary_key = "transaction"

    def path(self, **kwargs) -> str:
        
        return "sales/price/details"

    def request_params(
        self, stream_state: Mapping[str, Any], stream_slice: Mapping[str, any] = None, next_page_token: Mapping[str, Any] = None
    ) -> MutableMapping[str, Any]:

        params = super().request_params(stream_state=stream_state)
        cursor_value = stream_state.get(self.cursor_field) or timestamp_to_unix_epoch(self.config['start_date'])

        params = {
            "max_results": self.page_size,
            "transaction_status": self.config['transaction_status'],
            "start_date": cursor_value - 1000, #subtract 1 second to ensure no window gap is created
            "end_date": int(datetime.now(timezone.utc).timestamp()*1000)
        }

        if next_page_token:
            params.update(next_page_token)

        return params

class SalesCommissions(IncrementalHotmartStream):

    cursor_field = "emitted_at"
    primary_key = "transaction"

    def path(self, **kwargs) -> str:
        
        return "sales/commissions"

    def request_params(
        self, stream_state: Mapping[str, Any], stream_slice: Mapping[str, any] = None, next_page_token: Mapping[str, Any] = None
    ) -> MutableMapping[str, Any]:

        params = super().request_params(stream_state=stream_state)
        cursor_value = stream_state.get(self.cursor_field) or timestamp_to_unix_epoch(self.config['start_date'])

        params = {
            "max_results": self.page_size,
            "transaction_status": self.config['transaction_status'],
            "start_date": cursor_value - 1000, #subtract 1 second to ensure no window gap is created
            "end_date": int(datetime.now(timezone.utc).timestamp()*1000)
        }

        if next_page_token:
            params.update(next_page_token)

        return params

class Subscriptions(HotmartStream):

    primary_key = "subscription_id"

    def path(self, **kwargs) -> str:
        
        return "subscriptions"

    def request_params(
        self, stream_state: Mapping[str, Any], stream_slice: Mapping[str, any] = None, next_page_token: Mapping[str, Any] = None
    ) -> MutableMapping[str, Any]:

        params = {
            "max_results": self.page_size,
            "accession_date": timestamp_to_unix_epoch(self.config['start_date'])
        }

        if next_page_token:
            params.update(next_page_token)

        return params

# Source
class SourceHotmart(AbstractSource):
    def check_connection(self, logger, config) -> Tuple[bool, any]:

        try:
            authenticator = HotmartTokenAuthenticator(
                token_endpoint="https://api-sec-vlc.hotmart.com/security/oauth/token",
                client_id=config["client_id"],
                client_secret=config["client_secret"],
                basic_token=config["basic_token"]
            )
            authenticator._fetch_token()
            token = authenticator._token
            return token is not None, None
        except Exception as e:
            return False, e

    def streams(self, config: Mapping[str, Any]) -> List[Stream]:

        authenticator = HotmartTokenAuthenticator(
            token_endpoint="https://api-sec-vlc.hotmart.com/security/oauth/token",
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            basic_token=config["basic_token"]
        )

        return [
            SalesHistory(authenticator=authenticator, config=config),
            SalesPriceDetails(authenticator=authenticator, config=config),
            SalesCommissions(authenticator=authenticator, config=config),
            Subscriptions(authenticator=authenticator, config=config)
        ]
