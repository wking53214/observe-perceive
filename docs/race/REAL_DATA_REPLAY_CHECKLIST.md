# Checklist: replaying real readings through the alarm-burden tools

Use this the first time `alarm_burden.py` (and, for a pilot, `ticket_burden.py`) meet real patient data. The tools only count; the data they
read is clinical and identifying, so most of the list is about handling. Nothing here is legal advice or an approval: where an approval is
needed it says whose. A step you cannot tick is a reason to stop, not to improvise.

## A. Before any data moves

- [ ] **Permission.** The privacy officer (and the IRB, if this counts as research rather than quality improvement; they decide) has approved
      this use in writing. The approval is not stored in this repository.
- [ ] **Purpose and scope written down:** which unit, which dates, which age range, which question ("how many events and notifications per
      patient-day at settings X"). Nothing wider is extracted.
- [ ] **A named custodian** extracts and de-identifies. The analyst never sees the real identifiers or the salt (`DATA_PRIVACY_AND_RESEARCH.md`
      zones Z1 and Z2).
- [ ] **A controlled place to run it:** an encrypted disk, no outbound network, no cloud sync, no shared drive, access limited to named people.

## B. The extract

- [ ] **Columns exactly:** `patient_id, timestamp, heart_rate, oxygen_saturation, respiratory_rate, temperature, age_months`. Nothing else
      (no names, no record numbers, no free text, no location).
- [ ] **`patient_id` is a pseudonym** made by the custodian (a keyed hash with a secret salt the analyst does not hold), not the record number.
      The same patient keeps the same pseudonym across the whole extract.
- [ ] **Timestamps keep their minute-level spacing.** Do not coarsen them to the hour: the engine's five-minute escalation lock and the
      tools' one-hour repeat window depend on real intervals. If dates must be shifted for privacy, shift each patient by one fixed offset and say so in the source label (UTC day
      boundaries, and so patient-day counts, move with it).
- [ ] **Missing values stay empty**, not zero. An empty cell replays as NaN, which the engine treats as a data-integrity fault (a standing
      WARNING that pages). That is a real cost of gaps, so keep the gaps; never fill them.
- [ ] **Age in months, or empty.** An empty age is replayed as unknown and uses generic norms.
- [ ] **Know that every patient starts cold:** the engine has no history from before the first reading it sees, and the tool counts every
      event in the file (it cannot exclude a warm-up). Extract a few hours before the window you care about and say so in the source label,
      or accept the cold-start events as part of the number.
- [ ] **Row count and a checksum** recorded by the custodian, so the analyst can confirm they got everything.

## C. The run

- [ ] **Settings fixed first.** The `ClinicalSettings` JSON in force, or the shipped defaults, recorded by digest. Run the clinical owner's
      proposed settings as a second run, never instead of the first.
- [ ] **Run the baseline both ways**: default notification (a person is told only at CRITICAL, a severity increase, an unassessable reading)
      and `--min-regime warning`. The difference is the decision the clinical owner needs.
- [ ] **The source label says what the data is:** unit, dates, de-identification method, any date shift, warm-up. It is printed in the
      report and is part of the number.
- [ ] **Run it twice** and confirm the two reports are identical (the engine is deterministic); if they differ, stop.
- [ ] **Keep the report with its three identifiers:** engine logic digest, clinical settings digest, notification settings (all printed).

## D. Reading the result

- [ ] It counts what the engine does. It does not say a page was right, wanted or acted on; do not report it as accuracy or safety.
- [ ] Look at the busiest-patients share and the first-reading events before the headline rate: a few patients with a faulty probe can
      dominate.
- [ ] Look at unassessable readings and the data-integrity rule counts: a high number measures the extract's gaps, not the patients.
- [ ] A quiet result is not good news. Lone abnormal channels do not page (review sheet S1 to S10); pair the burden with the
      deterioration-event replay in the validation plan.
- [ ] Small counts (a single patient, a single night) can identify someone: the privacy officer sets the smallest cell that may leave the
      environment.

## E. If a pilot's escalation log exists

- [ ] The log holds patient identifiers (identifier data). Treat it like the extract: same place, same people.
- [ ] `ticket_burden.py` reads it read-only. A broken hash chain or half-written last line is reported and the figures stop there; exit
      status 2 means look at the log before trusting anything.
- [ ] Read time to acknowledge only beside the unacknowledged and exhausted counts.

## F. When finished

- [ ] The extract, the reports' working copies and any temporary files are destroyed on the schedule the privacy officer sets; the
      destruction is recorded.
- [ ] Only the report (counts, no identifiers) leaves the environment, after the small-cell check.
- [ ] The report, its identifiers and the approval reference are filed together, so the number can be reproduced or challenged.
