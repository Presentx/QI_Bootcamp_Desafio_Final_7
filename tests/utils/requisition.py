import json
from requests import request, Response
from requests.exceptions import ConnectionError as RequestsConnectionError
from os import environ


API_OFFLINE = (
    "Não consegui falar com a API em {base_url}.\n"
    "Ela precisa estar de pé pros testes rodarem. Suba com:  docker compose up"
)


class ClientRequisition:
    @staticmethod
    def send(
        method,
        endpoint,
        payload=None,
        headers=None,
        data=None,
        cert=None,
        query_params=None,
        verify=True,
    ):

        if headers is None:
            headers = dict()

        api_host = environ.get("SERVER_LOCALHOST", "0.0.0.0")
        api_port = environ.get("API_PORT", "3000")
        base_url = f"http://{api_host}:{api_port}"

        url = f"{base_url}{endpoint}"

        try:
            response = request(
                method.upper(),
                url,
                headers=headers,
                json=payload,
                data=data,
                cert=cert,
                verify=verify,
                params=query_params,
            )
        except RequestsConnectionError:
            raise RuntimeError(API_OFFLINE.format(base_url=base_url)) from None

        base_response = BaseConnectorResponse(
            endpoint=endpoint,
            method=method,
            payload=payload,
            headers=headers,
            response=response,
        )

        return base_response


class BaseConnectorResponse:
    def __init__(
        self,
        response: Response,
        endpoint: str,
        method: str,
        headers: dict,
        payload: dict,
    ) -> None:
        self.endpoint = endpoint
        self.method = method
        self.payload = payload
        self.headers = headers
        self.response = response
        self.response_content = response.content
        self.response_status = response.status_code

        self.response_json = None
        try:
            self.response_json = json.loads(self.response_content)
        except Exception as ex:
            print(ex)
            ...
            # logger warning
