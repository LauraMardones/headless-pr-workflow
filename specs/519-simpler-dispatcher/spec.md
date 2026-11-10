# Epic #519: Simplify dispatcher orchestration

**Epic issue:** #519
**Milestone:** <milestone>

## Goal

HPW:s dispatcher ska vara lättare att förstå, ändra och testa genom att samla beslut om nästa steg på ett tydligt ställe och minska överlappande kontrollflöde, utan att försvaga befintliga säkerhets- och mergegrindar.

## Scope

In:

- Inventera beslut och dubbla vägar i dispatcherns körning, särskilt i `scripts/dispatcher-invoke.sh`, och förenkla dem utan att ändra utfallet för en given uppsättning GitHub-fakta.
- Samla regler om nästa steg i testbar kod enligt ADR-014. Behåll Bash för körordning, sessionsstarter och utgångskoder.
- Låt befintliga flöden för förfining, implementering, granskning, fixrundor, merge, cleanup och avbrott använda samma beslutsregler och beviskrav.
- Lägg till regressionstester för de berörda tillståndsövergångarna och felvägarna.

Out:

- Att införa Codex som exekverare; det hör till Epic #518.
- Nya regler för vilka stories som väljs, när en PR får mergas eller vilka beslut som kräver PO.
- Att byta runner, autentiseringsmodell eller skriva om hela dispatchern i ett steg.
- Nya dashboards eller användargränssnitt.

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
