# Simple Parser — Working Design

This document records the simple, low-error parser design for the raw API payload dump. It is based on the observed structures in `seraRawPayloadDump_mock.txt` and will be extended as each parser question is answered.

## Question 1 — How should PAN be extracted?

### Evidence found in the dump

The dump does not use one consistent PAN location. The following patterns are present:

- The envelope commonly contains `pan`, `panNumber`, `loggedInUserId`, or `userid`.
- Bank/account responses commonly expose the identity as `raw_payload.entityNum`.
- ITR responses commonly expose `raw_payload.submitUserId`.
- Deep ITR payloads contain `ITR.*.PersonalInfo.PAN` and sometimes `AssesseeVerPAN`.
- GST responses contain `gstin`; in some records the field named `pan` actually contains a GSTIN such as `19MOKPA1003D1Z9`.
- Many records have an empty `pan`, so an empty `pan` must never be treated as evidence.
- The header `Client ID` and JSON `client_id` are not safe PAN sources. In the mock dump they can differ and are identifiers for the capture/client record, not necessarily the taxpayer PAN.

### Extraction order

The simple parser should use this order:

1. Read explicit PAN values from known identity fields:

   `pan`, `panNumber`, `pan_no`, `entityNum`, `submitUserId`, `loggedInUserId`, `userId`, `userid`, `PersonalInfo.PAN`, and `AssesseeVerPAN`.

2. Read valid GSTIN values from `gstin` and other known GST identity fields. Derive the PAN from positions 3–12 of the GSTIN.

3. Only if the known fields produce no candidate, recursively inspect JSON string values for valid PAN/GSTIN-shaped values. This is a fallback, not the primary method, because ordinary messages and error text can contain identity-like strings.

4. Normalize every candidate by trimming whitespace and converting to uppercase. Empty values, `null`, `None`, `N/A`, and malformed values are discarded.

### Validation rules

PAN must match exactly:

```text
^[A-Z]{5}[0-9]{4}[A-Z]$
```

GSTIN must match exactly:

```text
^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$
```

A GSTIN-derived PAN is accepted only after the GSTIN itself passes validation.

### Conflict handling

- One unique PAN candidate: accept it as the payload PAN.
- Multiple occurrences of the same PAN: accept it and retain all source paths.
- Multiple different PAN candidates: mark the payload as `ambiguous`; do not choose by frequency.
- No valid PAN, but one valid GSTIN: use the derived PAN and record `derived_from_gstin`.
- No valid identity: keep the payload in the raw event ledger, but leave PAN unresolved for later session/timestamp context.

### Evidence metadata to retain

Every extracted result should retain:

```text
pan
pan_sources
gstin
derived_from_gstin
identity_confidence
identity_status
```

Example:

```text
PAN: MOKPA1000A
Source: raw_payload.entityNum
Confidence: high
Status: explicit
```

The parser must never derive a PAN from the header `Client ID`, from an arbitrary numeric identifier, or from a single unvalidated text match.

## Question 2 — How should client names be extracted?

### Evidence found in the dump

The mock dump contains several genuine client-name patterns, but it also contains many fields whose names are not client names. Observed client-name sources include:

- `raw_payload.ITR.*.PersonalInfo.AssesseeName.FirstName`
- `raw_payload.ITR.*.PersonalInfo.AssesseeName.MiddleName`
- `raw_payload.ITR.*.PersonalInfo.AssesseeName.SurNameOrOrgName`
- `raw_payload.ITR.*.Verification.Declaration.AssesseeVerName`
- `raw_payload.firstName`, `raw_payload.midName`, and `raw_payload.lastName`
- `raw_payload.nameAsPerBank`
- `raw_payload.data.auth_name`
- GST records containing `bn`, `ln`, or `fullName`

The dump also contains non-client name fields such as `bankName`, `formName`, `fieldName`, `ResidenceName`, `FatherName`, `NameOfBusiness`, and `CategoryName`. These must not automatically become the client name.

### Extraction process

1. Parse only name fields from an allow-list of known paths. Do not search every key containing the word `name`.

2. Build a person name from component fields in this order:

   `FirstName` + `MiddleName` + `SurNameOrOrgName`, or `firstName` + `midName` + `lastName`.

3. Read complete-name fields separately, retaining their source path. For example, `AssesseeVerName`, `auth_name`, `nameAsPerBank`, `fullName`, and GST `ln` should not be silently merged as if they were the same field.

4. Normalize names by trimming, collapsing repeated whitespace, removing empty/null components, and preserving the meaningful original letter case only for display. Comparisons should use uppercase normalized text.

5. Reject placeholders such as `N/A`, `NA`, `NULL`, `NONE`, empty strings, and values that are clearly labels or technical messages.

6. Attach each name candidate to the PAN/GSTIN evidence from the same payload. A name without an identity is only an unassigned name observation; it must not create a new client.

### Source priority

For a single payload, use this confidence order:

1. ITR `PersonalInfo.AssesseeName` or `AssesseeVerName` — strongest taxpayer identity evidence.
2. Explicit portal identity fields such as `auth_name`, `fullName`, or GST legal-name field `ln`.
3. Composed `firstName`/`midName`/`lastName`.
4. `nameAsPerBank` — useful corroborating evidence, but it is specifically the bank-account holder name and may differ from the taxpayer or business name.
5. GST `bn` or business-name fields — store as `business_name`, not automatically as the person's name.

This priority is used to rank candidates, not to delete lower-ranked evidence.

### Conflict handling

- Same normalized name from multiple sources: keep one display value and retain all source paths.
- Different names for the same PAN: retain every candidate, rank them by source priority, and mark `name_status` as `conflicting` until corroborated.
- A name from a bank response alone: mark it `corroborating`, not definitive.
- A business name and a person name: store them in separate fields (`business_name` and `client_name`).
- No reliable name: leave the client name blank or `Unresolved`; never infer it from PAN characters.

### Evidence metadata to retain

```text
client_name
name_candidates
name_sources
business_name
name_confidence
name_status
```

The simple parser should therefore produce a conservative client profile rather than one guessed name. The Excel output may display the highest-confidence name while retaining alternate names and conflicts for review.

## Question 3 — How should client emails be extracted?

### Evidence found in the dump

The dump contains several email representations:

- `raw_payload.emailId` — portal email field.
- `raw_payload.priEmailId` — primary email field, with `priEmailRelationId` as separate metadata.
- `raw_payload.email` — shorter portal profile field.
- `raw_payload.ITR.*.PersonalInfo.Address.EmailAddress` — email inside an ITR address object.
- `raw_payload.itrPanDetlList[].activityTxt` — an escaped JSON string that contains historical `emailId` values and must be decoded before extraction.
- `emailVerFlag` describes verification state; it is not itself an email address.

### Extraction process

1. Parse the payload as JSON. If a field such as `activityTxt` contains JSON text, parse that nested string as JSON before inspecting it.

2. Read email values only from known email paths:

   `email`, `emailId`, `priEmailId`, `PersonalInfo.Address.EmailAddress`, and decoded `activityTxt.emailId`.

3. Normalize each value by trimming whitespace and converting the domain portion to lowercase. Preserve the local-part spelling for display, but compare addresses case-insensitively.

4. Validate the complete address with a conservative email shape check such as:

```text
^[^\s@]+@[^\s@]+\.[^\s@]+$
```

Reject empty values, `null`, `None`, `N/A`, values containing spaces, and values that fail the shape check. Do not reject an address merely because its domain is unfamiliar.

5. Attach the email to the PAN/GSTIN evidence from the same payload. An email must not create or identify a client by itself because the same mailbox can serve multiple taxpayers.

### Source and role classification

- `priEmailId` — classify as `primary_portal_email`.
- `emailId` or `email` — classify as `portal_email` unless the surrounding payload clearly identifies another role.
- ITR `EmailAddress` — classify as `itr_contact_email`.
- Decoded `activityTxt.emailId` — classify as `historical_communication_email`, not automatically as the current primary email.

Keep the source path, verification flag, timestamp/period context, and role with each candidate. The mock dump demonstrates that historical communication emails can change over time, so the parser must preserve all observed addresses rather than overwrite the old value.

### Conflict handling

- Same normalized email from multiple paths: keep one address with multiple evidence sources.
- Multiple different emails for one PAN: retain all candidates and mark the profile `multiple_emails`; select a `primary_email` only when `priEmailId` or another explicit primary field supports it.
- Email verification flag `Y`: record `verified`; `N`: record `unverified`; missing flag: record `unknown`.
- Email without a defensible PAN/GSTIN: keep it attached to the raw event only and do not assign it to a client container.

### Evidence metadata to retain

```text
primary_email
all_emails
email_sources
email_roles
email_verification_status
email_status
```

The simple parser should therefore treat email as a client attribute and corroborating evidence, never as a standalone identity key.

## Question 4 — How should client DOB be extracted?

### Evidence found in the dump

The mock dump contains DOB in three forms:

- `raw_payload.dob` — ISO date text such as `1985-08-15`.
- `raw_payload.dateOfBirth` — numeric epoch-millisecond values.
- `raw_payload.ITR.*.PersonalInfo.DOB` — nested ITR date text.

The payload also contains many other date/timestamp fields, including `createdTmstmp`, `lastLoginTmstmp`, `lastLogoutTmstmp`, `dscExpDt`, filing dates, and period labels. Those are not DOB evidence and must be excluded.

The dump contains an important conflict example: in one payload, `dateOfBirth` and `dob` do not represent the same calendar date. Therefore, the parser must not blindly prefer the first date-shaped value.

### Extraction process

1. Read only these allow-listed paths:

   `raw_payload.dob`, `raw_payload.dateOfBirth`, and `raw_payload.ITR.*.PersonalInfo.DOB`.

2. Parse text dates strictly. Accept ISO forms such as `YYYY-MM-DD`; support other formats only through an explicit, tested format list.

3. Parse numeric `dateOfBirth` as epoch milliseconds only when the value is numeric and converts to a plausible calendar date. Do not interpret arbitrary timestamps or date-like numbers as DOB.

4. Normalize every accepted value to one canonical format:

```text
YYYY-MM-DD
```

5. Validate the calendar date:

   - not before `1900-01-01`;
   - not after the current date;
   - reject impossible dates and invalid epoch conversions.

6. Attach the DOB candidate to the PAN/GSTIN evidence from the same payload. DOB must never be used by itself to create or identify a client.

### Confidence and conflict rules

- Same date from multiple fields: accept it and retain all source paths.
- One valid date from one field: accept it as `observed`, not automatically as definitive.
- Different valid dates in the same payload: mark `dob_status` as `conflicting`, retain every candidate, and do not silently choose one.
- A nested ITR `PersonalInfo.DOB` can be ranked highly, but it does not erase a contradictory value from another authoritative-looking field.
- If business rules later require a provisional value, use the source priority `ITR PersonalInfo.DOB` → `dob` → numeric `dateOfBirth`, while visibly marking the result as `provisional`.
- If no valid candidate exists, leave DOB unresolved.

### Evidence metadata to retain

```text
date_of_birth
dob_candidates
dob_sources
dob_status
dob_confidence
```

The safe default for this parser is therefore: extract, normalize, compare, and quarantine contradictions. A DOB should be displayed as `Unresolved` rather than guessed when the raw payload disagrees with itself.

## Question 5 — How should return type be extracted?

### Evidence found in the dump

Return/form information appears in several layers, and the top-level `filing_type` is not always reliable. The mock dump contains:

- GST endpoint query parameter `rtn_typ=GSTR1`.
- GST `raw_payload.formName` values such as `GSTR-1 / IFF`.
- GST `raw_payload.data.gstr1IFF.form` values such as `R1`.
- Income Tax `raw_payload.formTypeCd` and `raw_payload.formCd`, commonly `4` or `4S`.
- Nested ITR `FormName` values such as `ITR-4`.
- Top-level values such as `ITR-4`, `ITR-4S`, `R1`, and `FO-091-EVERI`.
- Generic labels such as `Profile / Contact Details`, `GST Taxpayer Profile`, and `Bank Validation`, which describe the interaction or screen, not necessarily a return type.

### Extraction process

1. Classify the endpoint first:

   - GST return endpoint: URL contains `formdetails`, `/gstr`, or a `rtn_typ` query parameter.
   - Income Tax return endpoint: URL contains `/returns/`, `submit/wzrd`, `validateOTP`, or ITR payload structures.
   - Profile, bank, login, download, and history endpoints: return type is `Not a return event` unless explicit return evidence is present.

2. For GST, use this source order:

   `url.rtn_typ` → `raw_payload.formName` → `raw_payload.data.gstr1IFF.form` → a non-generic top-level `filing_type`.

   Normalize `GSTR1` to `GSTR-1`; retain `IFF` when present. `R1` may be stored as the portal code with a normalized label only when the surrounding endpoint confirms it is a GST return.

3. For Income Tax, use this source order:

   nested `ITR.*.Form_*.FormName` → nested `ITR.*.FormName` → `raw_payload.formTypeCd` → `raw_payload.formCd` → a valid top-level `filing_type`.

   Numeric code `4` becomes `ITR-4` only when the endpoint or payload is clearly an Income Tax return. `4S` becomes `ITR-4S`. Do not apply the numeric conversion to an unrelated portal.

4. Treat `filingTypeCd` separately. In the dump, values such as `O`, `U`, and `R` describe filing mode/version (for example original, updated, or revised), not the return form itself. Store it as `filing_mode`, never as `return_type`.

5. Normalize equivalent representations, preserve the raw value, and retain the exact source path or URL query parameter.

### Conflict handling

- Same return type from multiple sources: accept it and retain all evidence.
- Different return types in one payload: mark `return_type_status` as `conflicting`; do not select by frequency.
- Generic top-level `filing_type` conflicts with a specific URL/form code: prefer the specific evidence and retain the generic label as `interaction_type`.
- No specific return evidence: set `return_type` to `Unknown` or `Not a return event`, rather than guessing from the portal name.

### Evidence metadata to retain

```text
return_type
return_type_raw
return_type_sources
filing_mode
interaction_type
return_type_confidence
return_type_status
```

The key rule is to separate the return form from the action and screen label. For example, `GST Taxpayer Profile` is not itself a return type, while `rtn_typ=GSTR1` is strong return-type evidence.

## Question 6 — How should submission and e-verification status be extracted?

### Evidence found in the dump

The dump contains several different kinds of status evidence:

- Income Tax submission endpoint: `/returns/submit/wzrd`.
- Successful Income Tax submission response: `httpStatus: "ACCEPTED"` and `successFlag: true`, usually with a transaction/ACK value.
- Income Tax OTP verification endpoint: `/verificationservices/auth/validateOTP`.
- Successful ITR verification: `moduleCode: "ITR"`, `status: "SUCCESS"`, and messages such as `OTP VALIDATED`.
- GST return endpoint: `formdetails?rtn_typ=GSTR1` with nested return status `FIL`.
- Historical return records: `filingStatus: "Filed"`, `filingStatus: "Not Filed"`, `statusDesc: "Pending for e-verification"`, and `statusDesc: "EVC Accepted"`.
- Other OTP/EVC activity, including bank or non-return modules, which must not be misclassified as return e-verification.

The outer dump field `Status: submitted` is useful context but is not sufficient by itself. The parser should rely primarily on endpoint and payload evidence.

### Process: build evidence flags first

For each payload, calculate independent flags instead of assigning a final label immediately:

```text
has_return_submission
has_return_filed_status
has_itr_everification
has_other_evc
has_pending_everification
has_failed_submission
```

#### Return submission evidence

Set `has_return_submission = true` only when one of these is present:

- Income Tax URL contains `/returns/submit/wzrd` and the payload has `httpStatus = ACCEPTED` with `successFlag = true`.
- GST return endpoint is identified and the response has a return status of `FIL` or an equivalent explicit filed status.
- A trusted portal-specific submission response explicitly says the return was filed/submitted.

Do not treat a login, profile update, bank save, history response, or outer `Status: submitted` as a return submission.

#### ITR e-verification evidence

Set `has_itr_everification = true` only when:

- URL contains `validateOTP`;
- `moduleCode = ITR`;
- verification status is successful, such as `status = SUCCESS` and/or `OTP VALIDATED`.

Historical evidence such as `statusDesc = EVC Accepted` may also count when it is attached to a defensible return identity and return period. `Pending for e-verification` is not successful verification.

#### Other EVC evidence

Set `has_other_evc = true` for successful OTP/EVC activity that is not tied to an ITR return—for example, a non-ITR module, bank-account EVC, or another explicit EVC action. It must remain separate from `has_itr_everification`.

### Link events before assigning the final category

Submission and verification events must be linked using this order:

1. Same PAN/GSTIN-derived PAN.
2. Same return type/form type.
3. Same filing period or assessment year when available.
4. Same ACK/ARN/transaction reference when available.
5. Same bounded session or a conservative timestamp window when references are absent.

If multiple clients or return periods are plausible, do not merge the events. Mark the result ambiguous and retain both evidence records.

### Final classification rules

Apply these rules per PAN + return type + filing period/assessment year episode:

1. `Submitted and e-verified`

   `has_return_submission = true` and a successfully linked ITR e-verification exists.

2. `Return submitted — not e-verified`

   `has_return_submission = true`, but no successfully linked ITR e-verification exists. If the payload explicitly says pending, include `pending e-verification` as a detail.

3. `E-verified only`

   A successful ITR e-verification exists, but no linked return-submission evidence exists in the same episode. This can represent a return submitted earlier or outside the captured session; it must not be rewritten as a new submission.

4. `Not submitted return`

   Return-related activity or a return-period record exists, but no submission evidence exists and no successful ITR e-verification is linked. A historical `Not Filed` status supports this result; profile/login/bank-only activity alone should be classified as `No return activity observed`, not `Not submitted return`.

5. `Other EVC event — no return submission`

   `has_other_evc = true`, but there is no return submission and no successful ITR e-verification. Record the module and event description so bank/non-return EVC is not mistaken for return verification.

6. `Ambiguous lifecycle`

   Evidence conflicts, references cannot be linked safely, or a payload claims a result without sufficient endpoint/status support. Keep all flags and source events for review.

### Evidence metadata to retain

```text
lifecycle_category
submission_evidence
everification_evidence
other_evc_evidence
pending_everification
linked_ack_or_transaction
lifecycle_confidence
lifecycle_status
```

The parser must classify from evidence flags and linked events, not from one text label. This prevents a bank EVC, a historical filing-status row, or an outer capture status from being incorrectly reported as a submitted and e-verified return.

## Question 7 — How should transaction numbers be extracted and used?

### Evidence found in the dump

The dump contains several identifier types that look similar but serve different purposes:

- Outer/header and envelope `arn` or `ARN / Ack No` — filing acknowledgement or captured result reference.
- `raw_payload.arnNumber` and `raw_payload.ackNum` — return acknowledgement references.
- `raw_payload.transactionNo` — portal transaction number; examples include `ITR...`, numeric values, and `EVERIFY...` values.
- `raw_payload.uniqueReqId` — request/correlation identifier, sometimes tied to bank validation.
- `raw_payload.reqId` and `itbaSequenceNo` — request/system sequence references; they may be null.
- Historical nested `activityTxt.ackNum`, `commRefNo`, and `receipt` — references from filing-history records.
- GST references such as ARN strings and transaction/reference values.

### Extraction process

1. Parse the payload recursively, including nested JSON strings such as `activityTxt`.

2. Read identifiers from an explicit allow-list:

   `arn`, `arnNumber`, `ackNum`, `transactionNo`, `uniqueReqId`, `reqId`, `itbaSequenceNo`, `ref_id`, `referenceId`, `commRefNo`, and `receipt`.

3. Normalize each value by converting it to trimmed text. Reject empty strings, `null`, `None`, `N/A`, and placeholder values such as `-`. Do not force all identifiers to numeric form: valid values in the dump include alphabetic prefixes such as `ITR...` and `EVERIFY...`.

4. Store every identifier with its exact source path and role. Do not collapse all values into one generic `transaction_number` field.

### Identifier role classification

- `acknowledgement`: `arn`, `arnNumber`, `ackNum`, and outer `ARN / Ack No` when tied to a filing result.
- `portal_transaction`: `transactionNo` for submission, payment, bank, or portal operations.
- `everification_transaction`: `transactionNo` beginning with `EVERIFY` or attached to a successful OTP validation.
- `request_correlation`: `uniqueReqId`, `reqId`, and `itbaSequenceNo`.
- `historical_filing_reference`: decoded `activityTxt.ackNum`, `commRefNo`, and `receipt`.
- `gst_reference`: GST ARN/reference values, retained alongside the normalized GST return event.

### Linking rules

Transaction identifiers should link events, not identify clients by themselves:

1. Exact shared `ackNum`/ARN is the strongest link between a return submission and its e-verification. The dump demonstrates that an e-verification payload can have its own `EVERIFY...` transaction number while carrying the submitted return’s `ackNum`.
2. Exact shared `transactionNo` can link retries or related responses when the endpoint and client identity agree.
3. `uniqueReqId`, `reqId`, and system sequence values are secondary correlation evidence, especially for bank operations.
4. Historical `commRefNo`/`receipt` values link historical records only when PAN, return type, and period also agree.
5. If identifiers conflict or point to multiple PANs, retain the conflict and mark the event ambiguous.

### Safety rules

- A transaction number is strong proof of a portal action when it is present in the correct endpoint response and accompanied by a success indicator. For Income Tax submission, `/returns/submit/wzrd` + a non-empty `transactionNo` + `httpStatus = ACCEPTED` or `successFlag = true` is high-confidence submission evidence. For GST, a return endpoint + a valid ARN/transaction reference + explicit `FIL`/filed status is high-confidence filing evidence.
- A transaction number alone must not assign a PAN or client. It proves an action/reference, not ownership; ownership still requires PAN/GSTIN or a defensible session link.
- Do not treat synthetic values such as `PROFILE-<PAN>` as filing acknowledgements.
- Keep the outer ARN, nested ARN, acknowledgement number, and transaction number as separate fields even when their values happen to match.
- Deduplicate repeated copies of the same identifier while preserving all evidence paths and entry numbers.

### Submission-proof rule

For lifecycle classification, rank evidence as follows:

1. **High confidence:** correct submission endpoint, non-empty transaction number, and explicit success response (`ACCEPTED`, `successFlag: true`, or GST `FIL`).
2. **Medium confidence:** correct submission endpoint and valid ARN/ACK, but the success flag is missing or inconclusive.
3. **Supporting only:** outer capture label such as `Status: submitted`, a transaction number from an unrelated endpoint, or a historical reference without a current submission response.

Therefore, transaction numbers should be preserved prominently in the parser output and used as primary submit-proof evidence when their endpoint and status context agree.

### Concrete lifecycle examples from the dump

#### Example A — Return submitted, not e-verified

Entry `#15` contains:

```text
URL: .../returns/submit/wzrd
httpStatus: ACCEPTED
successFlag: true
transactionNo: ITR000883649705
arnNumber: 677475180230826
```

If no matching successful ITR verification is found, the result must be:

```text
Return submitted — not e-verified
Submit proof: ITR000883649705
Link reference: 677475180230826
```

#### Example B — Return submitted and e-verified

Entry `#51` contains:

```text
URL: .../verificationservices/auth/validateOTP
moduleCode: ITR
status: SUCCESS
message: OTP VALIDATED
transactionNo: EVERIFY000920870466
ackNum: 677475180230826
```

Because `ackNum: 677475180230826` matches Entry `#15`'s `arnNumber`, the two events form one lifecycle:

```text
Return submitted and e-verified
Submit proof: ITR000883649705
E-verification proof: EVERIFY000920870466
Shared link: 677475180230826
```

#### Example C — EVC, no return submission

If the parser sees a successful EVC/OTP or historical `EVC Accepted` record but cannot link it to a return-submission proof for the same PAN, return type, and period, the result must be:

```text
EVC — no return submission
EVC proof: retained transaction/ACK/reference
Return submission proof: absent
```

This category is deliberately different from `E-verified only`: use `E-verified only` only when the successful event is explicitly an ITR return verification. Use `EVC — no return submission` for other EVC activity or an EVC record whose return association cannot be proven.

## Question 8 — How should return period be extracted?

### Evidence found in the dump

The dump contains two main period systems:

- Income Tax assessment years: header/envelope values such as `AY 2026-27`, plus `assessmntYr`, `assmentYear`, and nested ITR `AssessmentYear` values such as `2026`.
- GST return periods: URL query values such as `rtn_prd=072026`, which represent July 2026, and historical `retPrds[].monthYearName` values such as `Mar - 2026`.

The header/envelope `period_label` is useful when specific, but values such as `Profile Info`, empty strings, and missing values are not return periods. `filingDate`, `submitTmstmp`, and capture timestamps are action timestamps, not the return period.

### Extraction process

1. Identify the portal and return type first. Do not interpret a period without knowing whether it is an Income Tax assessment year or a GST tax period.

2. For Income Tax, use this source order:

   specific header/envelope `period_label` → `assessmntYr`/`assmentYear` → nested ITR `AssessmentYear` → a validated assessment-year field in the return payload.

   Normalize a numeric assessment year `2026` to `AY 2026-27`, while retaining the raw value. If the source says `AY 2026` and the ending year is not explicit, record both the normalized interpretation and the original label.

3. For GST, use this source order:

   URL query `rtn_prd` → specific envelope `period_label` → `retPrds[].monthYearName`.

   Parse `MMYYYY` only when the month is 01–12. For example, `rtn_prd=072026` becomes `Jul 2026`. Parse month-name strings only through an explicit month list.

4. Historical period arrays must produce one period observation per array item. Do not assign the latest period to every historical filing record. Pair each period with its own `filingStatus`, `filingDate`, ARN/ACK, and return type when available.

5. Reject generic labels (`Profile Info`, `Current`, empty, `N/A`) as return periods. Store them as interaction context only if needed.

### Conflict and linking rules

- Same normalized period from multiple fields: accept it and retain all source paths.
- Different period values in one payload: mark `period_status` as `conflicting`; do not silently choose one.
- Link a period to a lifecycle episode using PAN, return type, ACK/ARN/transaction reference, and session/timestamp context.
- A filing date proves when an action occurred; it must not replace a missing return period.
- If no defensible period exists, use `Unknown period` and keep the submission/e-verification evidence separate.

### Evidence metadata to retain

```text
return_period
period_type
period_raw
period_sources
period_status
period_confidence
```

The key rule is to separate the return period from the event timestamp: `AY 2026-27` or `Jul 2026` describes what was filed, while `Timestamp`, `filingDate`, and `submitTmstmp` describe when the portal action happened.

### Evidence metadata to retain

```text
acknowledgements
portal_transactions
everification_transactions
request_correlations
historical_references
transaction_sources
transaction_link_confidence
```

## Current implementation direction

This simple parser will remain separate from the existing advanced pipelines until its rules are tested against the full dump. Its main design priority is deterministic extraction: explicit field evidence first, validated GSTIN derivation second, conservative fallback scanning third, and quarantine on conflict.
