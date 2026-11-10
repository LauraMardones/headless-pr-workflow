# Epic #518: Codex as an unattended executor

**Epic issue:** #518
**Milestone:** <milestone>

## Goal

HPW ska kunna låta Codex på mitt nuvarande abonnemang förfina, implementera och göra fixrundor obemannat när min dator är igång. Claude granskar Codex-PR:er oberoende. Arbetet ska kunna återupptas efter avbrott och respektera abonnemangens gränser utan separat API-debitering.

## Scope

In:

- En schemalagd Codex-uppgift på PO:s dator hämtar tilldelat arbete och utför förfining, implementering och fixrundor utan att PO startar varje session.
- HPW registrerar ägarskap, framsteg, avbrott och överlämning som beständiga GitHub-fakta så att arbetet kan återupptas utan dubbelstart eller förlorade ändringar.
- Codex följer samma krav på tester, PR, sessionssammanfattning och granskning av aktuell head-SHA som Claude. Codex-PR:er går till oberoende Claude-granskning och vidare genom befintliga mergegrindar.
- Innan en session startar kontrolleras tillgänglig Codex-ranson och reserverad Claude-kapacitet för granskning. Vid en gräns eller otillgänglig dator väntar Codex-tilldelade stories och återupptas senare.
- Ett end-to-end-försök på riktiga stories visar att förfining, implementering, fixrundor, överlämning och återupptagning fungerar obemannat.

Out:

- API-debitering, Business- eller Enterprise-abonnemang och att kopiera en interaktiv inloggning till GitHub Actions.
- Automatisk överflyttning av Codex-tilldelade stories till Claude när Codex inte kan köra.
- Generell förenkling av dispatchern; Epic #519 äger den.
- Ändrade regler för oberoende granskning, merge, PO-beslut eller personligt godkännande.
- Driftsättning och underhåll av appar som HPW utvecklar.

## Acceptance Criteria

- **E1:** <…>
  - **Verified by:** <…>

## Decisions

<…>

## Checked Against

<…>

## Delegated to the Tech Lead

<…>

## Dependencies

<…>

## Open Questions

<…>
