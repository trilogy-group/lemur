"""
.. module: lemur.common.utils
    :platform: Unix
    :copyright: (c) 2018 by Netflix Inc., see AUTHORS for more
    :license: Apache, see LICENSE for more details.

.. moduleauthor:: Kevin Glisson <kglisson@netflix.com>
"""
import random
import string

import sqlalchemy
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, ec, padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from flask_restful.reqparse import RequestParser
from sqlalchemy import and_, func

from lemur.constants import CERTIFICATE_KEY_TYPES
from lemur.exceptions import InvalidConfiguration

paginated_parser = RequestParser()

paginated_parser.add_argument('count', type=int, default=10, location='args')
paginated_parser.add_argument('page', type=int, default=1, location='args')
paginated_parser.add_argument('sortDir', type=str, dest='sort_dir', location='args')
paginated_parser.add_argument('sortBy', type=str, dest='sort_by', location='args')
paginated_parser.add_argument('filter', type=str, location='args')


def get_psuedo_random_string():
    """
    Create a random and strongish challenge.
    """
    challenge = ''.join(random.choice(string.ascii_uppercase) for x in range(6))  # noqa
    challenge += ''.join(random.choice("~!@#$%^&*()_+") for x in range(6))  # noqa
    challenge += ''.join(random.choice(string.ascii_lowercase) for x in range(6))
    challenge += ''.join(random.choice(string.digits) for x in range(6))  # noqa
    return challenge


def parse_certificate(body):
    """
    Helper function that parses a PEM certificate.

    :param body:
    :return:
    """
    assert isinstance(body, str)

    return x509.load_pem_x509_certificate(body.encode('utf-8'), default_backend())


def parse_private_key(private_key):
    """
    Parses a PEM-format private key (RSA, DSA, ECDSA or any other supported algorithm).

    Raises ValueError for an invalid string. Raises AssertionError when passed value is not str-type.

    :param private_key: String containing PEM private key
    """
    assert isinstance(private_key, str)

    return load_pem_private_key(private_key.encode('utf8'), password=None, backend=default_backend())


def parse_csr(csr):
    """
    Helper function that parses a CSR.

    :param csr:
    :return:
    """
    assert isinstance(csr, str)

    return x509.load_pem_x509_csr(csr.encode('utf-8'), default_backend())


def get_authority_key(body):
    """Returns the authority key for a given certificate in hex format"""
    parsed_cert = parse_certificate(body)
    authority_key = parsed_cert.extensions.get_extension_for_class(
        x509.AuthorityKeyIdentifier).value.key_identifier
    return authority_key.hex()


def generate_private_key(key_type):
    """
    Generates a new private key based on key_type.

    Valid key types: RSA2048, RSA4096', 'ECCPRIME192V1', 'ECCPRIME256V1', 'ECCSECP192R1',
        'ECCSECP224R1', 'ECCSECP256R1', 'ECCSECP384R1', 'ECCSECP521R1', 'ECCSECP256K1',
        'ECCSECT163K1', 'ECCSECT233K1', 'ECCSECT283K1', 'ECCSECT409K1', 'ECCSECT571K1',
        'ECCSECT163R2', 'ECCSECT233R1', 'ECCSECT283R1', 'ECCSECT409R1', 'ECCSECT571R2'

    :param key_type:
    :return:
    """

    _CURVE_TYPES = {
        "ECCPRIME192V1": ec.SECP192R1(),
        "ECCPRIME256V1": ec.SECP256R1(),

        "ECCSECP192R1": ec.SECP192R1(),
        "ECCSECP224R1": ec.SECP224R1(),
        "ECCSECP256R1": ec.SECP256R1(),
        "ECCSECP384R1": ec.SECP384R1(),
        "ECCSECP521R1": ec.SECP521R1(),
        "ECCSECP256K1": ec.SECP256K1(),

        "ECCSECT163K1": ec.SECT163K1(),
        "ECCSECT233K1": ec.SECT233K1(),
        "ECCSECT283K1": ec.SECT283K1(),
        "ECCSECT409K1": ec.SECT409K1(),
        "ECCSECT571K1": ec.SECT571K1(),

        "ECCSECT163R2": ec.SECT163R2(),
        "ECCSECT233R1": ec.SECT233R1(),
        "ECCSECT283R1": ec.SECT283R1(),
        "ECCSECT409R1": ec.SECT409R1(),
        "ECCSECT571R2": ec.SECT571R1(),
    }

    if key_type not in CERTIFICATE_KEY_TYPES:
        raise Exception("Invalid key type: {key_type}. Supported key types: {choices}".format(
            key_type=key_type,
            choices=",".join(CERTIFICATE_KEY_TYPES)
        ))

    if 'RSA' in key_type:
        key_size = int(key_type[3:])
        return rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend()
        )
    elif 'ECC' in key_type:
        return ec.generate_private_key(
            curve=_CURVE_TYPES[key_type],
            backend=default_backend()
        )


def check_cert_signature(cert, issuer_public_key):
    """
    Check a certificate's signature against an issuer public key.
    Before EC validation, make sure public key and signature are of the same type,
    otherwise verification not possible (raise InvalidSignature)
    On success, returns None; on failure, raises UnsupportedAlgorithm or InvalidSignature.
    """
    if isinstance(issuer_public_key, rsa.RSAPublicKey):
        # RSA requires padding, just to make life difficult for us poor developers :(
        if cert.signature_algorithm_oid == x509.SignatureAlgorithmOID.RSASSA_PSS:
            # In 2005, IETF devised a more secure padding scheme to replace PKCS #1 v1.5. To make sure that
            # nobody can easily support or use it, they mandated lots of complicated parameters, unlike any
            # other X.509 signature scheme.
            # https://tools.ietf.org/html/rfc4056
            raise UnsupportedAlgorithm("RSASSA-PSS not supported")
        else:
            padder = padding.PKCS1v15()
        issuer_public_key.verify(cert.signature, cert.tbs_certificate_bytes, padder, cert.signature_hash_algorithm)
    elif isinstance(issuer_public_key, ec.EllipticCurvePublicKey) and isinstance(cert.signature_hash_algorithm, ec.ECDSA):
            issuer_public_key.verify(cert.signature, cert.tbs_certificate_bytes, cert.signature_hash_algorithm)
    else:
        raise InvalidSignature


def is_selfsigned(cert):
    """
    Returns True if the certificate is self-signed.
    Returns False for failed verification or unsupported signing algorithm.
    """
    try:
        check_cert_signature(cert, cert.public_key())
        # If verification was successful, it's self-signed.
        return True
    except InvalidSignature:
        return False
    except UnsupportedAlgorithm as e:
        raise Exception(e)


def is_weekend(date):
    """
    Determines if a given date is on a weekend.

    :param date:
    :return:
    """
    if date.weekday() > 5:
        return True


def validate_conf(app, required_vars):
    """
    Ensures that the given fields are set in the applications conf.

    :param app:
    :param required_vars: list
    """
    for var in required_vars:
        if var not in app.config:
            raise InvalidConfiguration("Required variable '{var}' is not set in Lemur's conf.".format(var=var))


# https://bitbucket.org/zzzeek/sqlalchemy/wiki/UsageRecipes/WindowedRangeQuery
def column_windows(session, column, windowsize):
    """Return a series of WHERE clauses against
    a given column that break it into windows.

    Result is an iterable of tuples, consisting of
    ((start, end), whereclause), where (start, end) are the ids.

    Requires a database that supports window functions,
    i.e. Postgresql, SQL Server, Oracle.

    Enhance this yourself !  Add a "where" argument
    so that windows of just a subset of rows can
    be computed.

    """
    def int_for_range(start_id, end_id):
        if end_id:
            return and_(
                column >= start_id,
                column < end_id
            )
        else:
            return column >= start_id

    q = session.query(
        column,
        func.row_number().over(order_by=column).label('rownum')
    ).from_self(column)

    if windowsize > 1:
        q = q.filter(sqlalchemy.text("rownum %% %d=1" % windowsize))

    intervals = [id for id, in q]

    while intervals:
        start = intervals.pop(0)
        if intervals:
            end = intervals[0]
        else:
            end = None
        yield int_for_range(start, end)


def windowed_query(q, column, windowsize):
    """"Break a Query into windows on a given column."""

    for whereclause in column_windows(
            q.session,
            column, windowsize):
        for row in q.filter(whereclause).order_by(column):
            yield row


def truthiness(s):
    """If input string resembles something truthy then return True, else False."""

    return s.lower() in ('true', 'yes', 'on', 't', '1')


def find_matching_certificates_by_hash(cert, matching_certs):
    """Given a Cryptography-formatted certificate cert, and Lemur-formatted certificates (matching_certs),
    determine if any of the certificate hashes match and return the matches."""
    matching = []
    for c in matching_certs:
        if parse_certificate(c.body).fingerprint(hashes.SHA256()) == cert.fingerprint(hashes.SHA256()):
            matching.append(c)
    return matching
