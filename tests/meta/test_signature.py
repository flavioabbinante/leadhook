from leadhook.meta.signature import verify_signature
from tests.fixtures import sign

BODY = b'{"object":"page"}'


def test_valid_signature():
    assert verify_signature("app-secret", BODY, sign(BODY))


def test_wrong_secret():
    assert not verify_signature("other", BODY, sign(BODY))


def test_tampered_body():
    assert not verify_signature("app-secret", BODY + b" ", sign(BODY))


def test_missing_or_malformed_header():
    assert not verify_signature("app-secret", BODY, None)
    assert not verify_signature("app-secret", BODY, "sha1=abc")
    assert not verify_signature("app-secret", BODY, "")


def test_non_ascii_signature_is_rejected_without_error():
    assert not verify_signature("app-secret", BODY, "sha256=\u00e9")
