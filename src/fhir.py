"""Read a patient's allergies from an HL7 FHIR R4 server.

Raw HTTP and FHIR JSON stay in this module. Callers get a `FhirPatientContext`,
and every failure surfaces as `FhirPatientNotFound` or `FhirUnavailable`.
fhir.resources ships R4B, which is wire-compatible with R4 for
the resources used here.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from fhir.resources.R4B.allergyintolerance import AllergyIntolerance
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.resource import Resource
from pydantic import ValidationError

FHIR_ID_PATTERN = r"^[A-Za-z0-9\-.]{1,64}$"
PAGE_SIZE = 100
MAX_PAGES = 5

# A missing status keeps the allergy, because an unconfirmed allergy is safer to show than to drop.
DROPPED_CLINICAL_STATUSES = frozenset({"inactive", "resolved"})
DROPPED_VERIFICATION_STATUSES = frozenset({"refuted", "entered-in-error"})

logger = logging.getLogger(__name__)


class FhirError(Exception):
    pass


class FhirPatientNotFound(FhirError):
    pass


class FhirUnavailable(FhirError):
    pass


@dataclass(frozen=True)
class FhirAllergy:
    resource_id: str
    text: str


@dataclass(frozen=True)
class FhirPatientContext:
    patient_reference: str
    allergies: list[FhirAllergy]


def _codes(concept: CodeableConcept | None) -> set[str]:
    return {coding.code for coding in (concept.coding or []) if coding.code} if concept else set()


def _is_current(allergy: AllergyIntolerance) -> bool:
    return not (
        _codes(allergy.clinicalStatus) & DROPPED_CLINICAL_STATUSES
        or _codes(allergy.verificationStatus) & DROPPED_VERIFICATION_STATUSES
    )


def _text(concept: CodeableConcept | None) -> str | None:
    if concept is None:
        return None
    first = concept.coding[0] if concept.coding else None
    for candidate in (concept.text, first and first.display, first and first.code):
        if candidate and candidate.strip():
            return str(candidate.strip())
    return None


def _current_allergies(resources: list[AllergyIntolerance], patient: str) -> list[FhirAllergy]:
    allergies = []
    skipped = 0
    for resource in resources:
        if not _is_current(resource):
            continue
        text = _text(resource.code)
        if text is None:
            skipped += 1
            continue
        allergies.append(FhirAllergy(resource_id=str(resource.id), text=text))
    if skipped:
        logger.warning("Skipped %d current allergies of %s with no usable text.", skipped, patient)
    return allergies


class FhirClient:
    def __init__(
        self, base_url: str, timeout: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._base = base_url.rstrip("/") + "/"
        self._http = httpx.Client(
            timeout=timeout, transport=transport, headers={"Accept": "application/fhir+json"}
        )

    def close(self) -> None:
        self._http.close()

    def patient_context(self, patient_id: str) -> FhirPatientContext:
        if not re.fullmatch(FHIR_ID_PATTERN, patient_id):
            raise FhirPatientNotFound(patient_id)
        reference = f"Patient/{patient_id}"
        self._read(reference, Patient, missing=FhirPatientNotFound(patient_id))
        return FhirPatientContext(
            patient_reference=reference,
            allergies=_current_allergies(self._allergies(reference), reference),
        )

    def _allergies(self, reference: str) -> list[AllergyIntolerance]:
        url: str | None = f"AllergyIntolerance?patient={reference}&_count={PAGE_SIZE}"
        resources: list[AllergyIntolerance] = []
        for _ in range(MAX_PAGES):
            if url is None:
                return resources
            bundle = self._read(url, Bundle)
            for entry in bundle.entry or []:
                if isinstance(entry.resource, AllergyIntolerance):
                    if not entry.resource.id:
                        raise FhirUnavailable(f"An allergy of {reference} has no id.")
                    resources.append(entry.resource)
            url = next((link.url for link in bundle.link or [] if link.relation == "next"), None)
        if url is not None:
            raise FhirUnavailable(f"{reference} has more than {MAX_PAGES} pages of allergies.")
        return resources

    def _read[R: Resource](self, url: str, model: type[R], missing: FhirError | None = None) -> R:
        absolute = urljoin(self._base, url)
        if urlsplit(absolute)[:2] != urlsplit(self._base)[:2]:
            raise FhirUnavailable(f"Refusing to follow a link off the FHIR server: {absolute}")
        try:
            response = self._http.get(absolute)
        except httpx.HTTPError as error:
            raise FhirUnavailable(f"GET {absolute}: {error}") from error
        if missing is not None and response.status_code in (404, 410):
            raise missing
        if response.status_code != 200:
            raise FhirUnavailable(f"GET {absolute}: HTTP {response.status_code}")
        try:
            return model.model_validate_json(response.content)
        except ValidationError as error:
            raise FhirUnavailable(f"GET {absolute}: not a FHIR {model.__name__}") from error
