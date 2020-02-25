"""
The main api class which will be used to authenticate and connect to the spotify api
"""

import asyncio
import base64
import json
import subprocess
import time
import webbrowser
from json import JSONDecodeError
from multiprocessing import Process
from subprocess import CompletedProcess
from typing import Tuple
from urllib.parse import urlencode

from aiohttp import ClientSession, TCPConnector, ClientTimeout

from .endpoints.albums import Albums
from .endpoints.artist import Artist

from .status_codes import STATUS_CODES
from .urls import URLS
from ..authentification.callback_server import create_callback_server
from ..authentification.spotify_authorization_token import SpotifyAuthorisationToken
from ..preferences import Preferences
from ..spotify_errors import SpotifyError


class API:
    """
    The main api class which will be used to authenticate and connect to the spotify api.
    Use this class to authenticate and connect to the spotify api.
    """

    # noinspection PyTypeChecker
    def __init__(self, preferences: Preferences, hold_authentication=False):
        """
        Create a new api class
        :param preferences: The preferences object fully filled with information
        :param hold_authentication: Should the api keep the authentication im memory and refresh it automatically
        (default = False)
        """

        # Check if the preferences are valid
        if not preferences.validate():
            raise SpotifyError("The preferences of your app are not correct")

        # Set the preferences
        self.preferences: Preferences = preferences
        self.hold_authentication: bool = hold_authentication
        self.session: ClientSession = None
        self.spotify_authorisation_token: SpotifyAuthorisationToken = None

        # Add all the api endpoints
        self.album: Albums = Albums(self)
        self.artist: Artist = Artist(self)

    async def create_new_client(self, request_timeout: int = 30, request_limit: int = 500) -> None:
        """
        Create a new session which will be used to connect to the spotify api.
        In general this only has to be called once after you create a new API object.
        You can however call this method if you want ot update the client settings (more requests, ...)
        This will however close all ongoing requests.
        :param request_timeout: How long should be waited for a request (default 30s) (None for no limit)
        :param request_limit: How many requests should be allowed (default 500)
        :return: None
        """

        if self.session:
            await self.session.close()

        timeout = ClientTimeout(total=request_timeout)
        connector = TCPConnector(limit=request_limit, enable_cleanup_closed=True)
        self.session = ClientSession(connector=connector, timeout=timeout)

    async def close_client(self) -> None:
        """
        Close the current client session. You have to create a new one to connect again to spotify.
        This method should always be called before you end your program
        :return: None
        """

        if self.session:
            await self.session.close()

    def build_authorization_url(self, show_dialog=True, state: str = None) -> str:
        """
        Builds the URL for the authorisation
        :param state: State of the authorization
        :param show_dialog: Should the spotify auth dialog be shown
        :return: The encoded url
        """

        params = {
            "client_id": self.preferences.application_id,
            "response_type": "code",
            "scope": ' '.join(self.preferences.scopes),
            "show_dialog": f"{show_dialog}",
            "redirect_uri": f"{self.preferences.redirect_url}"
        }

        # Check if a state is required
        if state:
            params["state"] = f"{state}"

        return f"{URLS.AUTHORIZE}?{urlencode(params)}"

    def open_oauth_dialog_in_browser(self, show_dialogue: bool = True) -> None:
        """
        Open the url in browser
        Only for testing purposes or the usage of this library in a desktop app
        :param show_dialogue: Should the spotify auth dialog be shown
        :return: None
        """

        # Open url in a new window of the default browser, if possible
        webbrowser.open_new(self.build_authorization_url(show_dialogue))

    async def get_code_with_cookie(self, cookie_file_location: str, callback_server_port: int = 1234,
                                   callback_server_url: str = "/test/api/callback") -> str:
        """
        This function takes care of the user interaction that is normally necessary to get the first code from spotify
        which is necessary to request the refresh_token and the oauth_token.
        The token that is returned by this function has to be passed to API.refresh_token(code, reauthorize=False)
        to get the refresh_token and the oauth_token.
        This will only work if the user has at least once accepted the scopes your app is requesting.
        I would recommend that you take a look at the source code of this function before you use it and that you are
        familiar with the authorization mechanism of spotify.
        This method is intended for automated testing. You have to decide if you want to use it in you production
        environment.
        :param cookie_file_location: The absolute path to the cookie file with all the active cookies in you browser
        when you visit
        https://open.spotify.com. The format is the same one used by curl.
        Take a look at this post if you wat to know the format of the cookies in the file:
        https://stackoverflow.com/questions/7181785/send-cookies-with-curl.
        To download the cookies you can use this extension. I don't know who wrote it and it is not open source so
        download and use it with care.
        https://chrome.google.com/webstore/detail/cookiestxt/njabckikapfpffapmjgojcnbfjonfjfg
        :param callback_server_port: The port the callback server will use to display the callback of the spotify api
        :param callback_server_url: The url the callback server will listen for callbacks for. Don't forget to add the
        url http://localhost:1111/test/api/callback to the allowed urls in the panel of you spotify app
        (developer.spotify.com)

        :raises SpotifyError if the command was not successful
        :raises UnicodeDecodeError if the returned string could not be decoded
        :raises JSONDecodeError if the returned and decoded string is not a valid json

        :return: The spotify code that can be used to get a refresh_token and a oauth_token
        """

        # Build the auth url
        url = self.build_authorization_url(show_dialog=False)

        # Create a webserver that runs in another process
        webserver_process: Process = create_callback_server(callback_server_port, callback_server_url)

        # Give the server some time to start
        await asyncio.sleep(3)

        return_code = self._curl_spotify_code(webserver_process, url, cookie_file_location)

        # Stop the webserver thread
        webserver_process.kill()
        return return_code

    @staticmethod
    def _curl_spotify_code(webserver_process: Process, url: str, cookie_file_location: str) -> str:
        """
        Curl the spotify code from the earlier created webserver
        :param webserver_process: The independent process that has been started to serve as callback server for spotify
        :param url: The url of the spotify authorization
        :param cookie_file_location: The location of the cookie file

        :raises SpotifyError if the command was not successful
        :raises UnicodeDecodeError if the returned string could not be decoded
        :raises JSONDecodeError if the returned and decoded string is not a valid json

        :return: The code returned by spotify
        """

        # Curl the code from spotify
        response: CompletedProcess = subprocess.run(['curl', '-L', '--cookie', f'{cookie_file_location}', f"{url}"],
                                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Check if the return code is correct (otherwise raise exception)
        if response.returncode != 0:
            raise SpotifyError(response.stderr)

        # Decode the returned byte string
        try:
            curl_str: str = response.stdout.decode()
        except UnicodeDecodeError:
            webserver_process.kill()
            raise SpotifyError("The returned code could not be decoded." + response.stderr.decode())

        # Get the decoded json code
        try:
            # TODO logging
            return_value: str = json.loads(curl_str)["code"]
        except JSONDecodeError or KeyError:
            # TODO logging
            webserver_process.kill()
            raise SpotifyError("The returned code could not be parsed as a json. Is the cookie file the right one?"
                               + response.stderr.decode())

        return return_value

    async def refresh_token(self, passed_auth_token_object: SpotifyAuthorisationToken = None,
                            reauthorize: bool = True,
                            code: str = None) -> SpotifyAuthorisationToken:
        """
        Refresh the auth token with the refresh token or get a new auth token and refresh token with the code returned
        by the spotify auth flow
        :param passed_auth_token_object: The refresh token or the code returned by the spotify auth flow
        :param reauthorize: Do want to reauthorize a expiring SpotifyAuthorisationToken or get a new one with the
        spotify code. Set to false and add the code="your_code_here" if you want to get the SpotifyAuthorisationToken
        for the first time
        :param code: The code returned by spotify and the OAuth
        :return: The SpotifyAuthorisationToken
        """

        grant_type: str = "refresh_token"
        if not reauthorize:
            grant_type = "authorization_code"

        body: dict = {
            "grant_type": grant_type,
        }

        if reauthorize:
            body["refresh_token"] = passed_auth_token_object.refresh_token
        else:
            body["code"] = code
            body["redirect_uri"] = self.preferences.redirect_url

        base_64: base64 = base64.b64encode(
            f"{self.preferences.application_id}:{self.preferences.application_secret}".encode("ascii"))
        header: dict = {'Authorization': f'Basic {base_64.decode("ascii")}'}

        if not self.session:
            raise SpotifyError("You have to create a new session with API.create_new_client() to connect to spotify")

        async with self.session.post(url=URLS.REFRESH, data=body, headers=header) as response:
            response_ok = self.request_ok(response.status)
            response_text: str = await response.text()

        response_text: dict = json.loads(response_text)

        # The response was not ok
        if not response_ok[0]:
            raise SpotifyError(response_ok[1] + "\n" + str(response_text))

        if "refresh_token" not in response_text:
            refresh_token = passed_auth_token_object.refresh_token
        else:
            refresh_token = response_text["refresh_token"]
        spotify_authorisation_token = SpotifyAuthorisationToken(refresh_token=refresh_token,
                                                                activation_time=int(time.time()),
                                                                access_token=response_text["access_token"])
        # Keep the auth token in memory
        if self.hold_authentication:
            self.spotify_authorisation_token = spotify_authorisation_token

        return spotify_authorisation_token

    @staticmethod
    def request_ok(status_code: int) -> Tuple[bool, str]:
        """
        Check if the returned status code is ok
        :param status_code: The status code that should be checked
        :return:
                [0] Is the response a success code
                [1] What does the response code mean
        """

        if status_code in STATUS_CODES["OK"]:
            return True, STATUS_CODES["OK"][status_code][0]

        if status_code in STATUS_CODES["REDIRECT"]:
            return False, STATUS_CODES["REDIRECT"][status_code][0]

        if status_code in STATUS_CODES["CLIENT_ERROR"]:
            return False, STATUS_CODES["CLIENT_ERROR"][status_code][0]

        if status_code in STATUS_CODES["SERVER_ERROR"]:
            return False, STATUS_CODES["SERVER_ERROR"][status_code][0]

        return False, "Unknown response code"