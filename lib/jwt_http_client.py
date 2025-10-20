# Copyright © 2023 Roblox Corporation
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
# associated documentation files (the “Software”), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial
# portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
# LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import importlib
import aiohttp
from json import JSONDecodeError
from pyjwt_key_fetcher.http_client import HTTPClient
from pyjwt_key_fetcher.errors import JWTHTTPFetchError

# Recarrega módulos se o script for reimportado dentro do Blender
if "bpy" in locals():
    if "create_http_client" in locals():
        importlib.reload(create_http_client)


class JWTHTTPClient(HTTPClient):
    """
    A custom HTTP client for JWT AsyncKeyFetcher implemented using aiohttp,
    ensuring SSL validation via certifi.
    """

    async def get_json(self, url: str) -> dict:
        """
        Fetch and parse JSON data from a URL.

        :param url: The URL to fetch data from.
        :return: Parsed JSON as a Python dict.
        :raise JWTHTTPFetchError: On network, decoding, or protocol errors.
        """
        if not url.startswith("https://"):
            raise JWTHTTPFetchError("Unsupported protocol in 'iss' (only HTTPS allowed).")

        try:
            # Importa o criador de cliente HTTP com fallback seguro
            try:
                from .create_http_client import create_http_client
            except ImportError:
                from create_http_client import create_http_client

            async with create_http_client() as session:
                response_data = {}
                async with session.get(url) as response:
                    try:
                        response_data = await response.json()
                        response.raise_for_status()
                        return response_data

                    except aiohttp.ClientResponseError as exception:
                        error_description = response_data.get("error_description", None)
                        if error_description:
                            exception.message = error_description
                        raise JWTHTTPFetchError(
                            f"Failed to fetch or decode {url}:\n{error_description or str(exception)}"
                        ) from exception

        except (aiohttp.ClientError, JSONDecodeError) as e:
            raise JWTHTTPFetchError(f"Failed to fetch or decode {url}:\n{str(e)}") from e
