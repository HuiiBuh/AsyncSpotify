"""
A file with a wrapper functions
"""
import async_spotify
from ..spotify_errors import SpotifyError


def get_url(url: str):
    """
    Wrap a get function
    :param url: The url that should be called
    :return: The executable function
    """

    def outer_wrapper(function):
        """
        Return the wrapper function so the wrapper function can be called
        :param function: The function that should be called
        :return:
        """

        async def wrapper(*args, **kwargs):
            """
            The wrapper that wraps the function to get the function arguments
            :param args: The args of the function
            :param kwargs: The keyword args of the function
            :return: The result of the function
            """

            api = args[0].api_object  # type: async_spotify.API
            query_params: dict = function(*args, **kwargs)

            async with api.session.get(url, params=query_params) as response:
                response_code = api.request_ok(response.status)
                response_json: dict = await response.json()

            if not response_code[0]:
                raise SpotifyError(response_json)

            return response_json

        return wrapper

    return outer_wrapper

# TODO add optional arg auth_token: SpotifyAuthToken
# TODO save auth
# TODO add the header retrieval