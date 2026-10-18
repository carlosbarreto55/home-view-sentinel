import socket
import time

import pytest

from sentinel.rtsp_probe import (
    DigestChallenge,
    ProbeResult,
    digest_authorization,
    encode_credential,
    parse_parameters,
    probe,
    stream_url,
)

PASSWORD = "a@b:c/d#e"


def run(server, user="sentinel", password=PASSWORD, **options):
    return probe("127.0.0.1", server.port, user, password, **options)


@pytest.mark.parametrize("mode", ["digest", "digest-qop", "basic"])
def test_accepted(fake_rtsp, mode):
    server = fake_rtsp(mode=mode)
    assert run(server) is ProbeResult.ACCEPTED
    assert server.accepted_logins == 1
    assert server.failed_logins == 0


@pytest.mark.parametrize("mode", ["digest", "digest-qop", "basic"])
def test_wrong_password_is_one_failed_login(fake_rtsp, mode):
    server = fake_rtsp(mode=mode)
    assert run(server, password="wrong") is ProbeResult.WRONG_CREDENTIALS
    assert server.failed_logins == 1


def test_wrong_user(fake_rtsp):
    server = fake_rtsp()
    assert run(server, user="Sentinel") is ProbeResult.WRONG_CREDENTIALS
    assert server.failed_logins == 1


def test_accepted_with_404_for_a_channel_without_camera(fake_rtsp):
    server = fake_rtsp(accepted_status="404 Not Found")
    assert run(server, channel=7) is ProbeResult.ACCEPTED


def test_server_without_authentication(fake_rtsp):
    assert run(fake_rtsp(mode="open")) is ProbeResult.ACCEPTED


def test_server_closing_after_the_challenge_is_asked_again(fake_rtsp):
    server = fake_rtsp(close_after_challenge=True)
    assert run(server) is ProbeResult.ACCEPTED
    assert server.connections == 2
    assert server.failed_logins == 0


def test_silent_server_times_out(fake_rtsp):
    server = fake_rtsp(mode="silent")
    start = time.monotonic()
    assert run(server, timeout=0.5) is ProbeResult.NOT_FOUND
    assert time.monotonic() - start < 1.5


def test_not_an_rtsp_server(fake_rtsp):
    assert run(fake_rtsp(mode="garbage")) is ProbeResult.NOT_FOUND


def test_nothing_listening():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert probe("127.0.0.1", port, "u", "p", timeout=1) is ProbeResult.NOT_FOUND


def test_request_uri_has_no_credentials(fake_rtsp):
    server = fake_rtsp()
    run(server, channel=3, subtype=0)
    first_lines = [request.split("\r\n")[0] for request in server.requests]
    expected = (
        f"DESCRIBE rtsp://127.0.0.1:{server.port}/cam/realmonitor?channel=3&subtype=0 RTSP/1.0"
    )
    assert first_lines == [expected, expected]
    assert all(PASSWORD not in request for request in server.requests)


def test_digest_response_without_qop_matches_rfc_2617_example():
    # RFC 2617 section 3.5, with the method and uri of the example and no qop.
    challenge = DigestChallenge(
        realm="testrealm@host.com", nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093"
    )
    header = digest_authorization(challenge, "GET", "/dir/index.html", "Mufasa", "Circle Of Life")
    assert 'response="670fd8c2df070c60b045671b8b24ff02"' in header
    assert "qop" not in header


def test_digest_response_with_qop_matches_rfc_2617_example():
    challenge = DigestChallenge.parse(
        'Digest realm="testrealm@host.com", qop="auth,auth-int", '
        'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", opaque="5ccc069c403ebaf9f0171e9517f40e41"'
    )
    header = digest_authorization(
        challenge, "GET", "/dir/index.html", "Mufasa", "Circle Of Life", client_nonce="0a4f113b"
    )
    assert 'response="6629fae49393a05397450978507c4ef1"' in header
    assert "qop=auth, nc=00000001" in header
    assert 'opaque="5ccc069c403ebaf9f0171e9517f40e41"' in header


def test_challenge_without_nonce_is_not_usable():
    assert DigestChallenge.parse('Digest realm="x"') is None
    assert DigestChallenge.parse('Basic realm="x"') is None


def test_parse_parameters_handles_quotes_and_tokens():
    assert parse_parameters('realm="a, \\"b\\"", nonce=abc , stale=FALSE') == {
        "realm": 'a, "b"',
        "nonce": "abc",
        "stale": "FALSE",
    }


def test_encode_credential_keeps_only_unreserved():
    assert encode_credential("a@b:c/d#e?f") == "a%40b%3Ac%2Fd%23e%3Ff"
    assert encode_credential("A-z_0.9~") == "A-z_0.9~"
    assert encode_credential("é ") == "%C3%A9%20"


def test_stream_url_for_logs():
    assert stream_url("192.0.2.10", 554, 1, 0) == (
        "rtsp://192.0.2.10:554/cam/realmonitor?channel=1&subtype=0"
    )
