# Epic #519: Simplify dispatcher orchestration

**Epic issue:** #519
**Milestone:** M6 Autonomous Execution

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

- **E1:** För samma GitHub-fakta väljer dispatchern samma nästa steg, blockerare och mergegrind före och efter förenklingen, även vid fel och avbrott.
  - **Verified by:** En scenario- och regressionstestmatris jämför beslut och synliga GitHub-effekter för förfining, implementering, granskning, fixrundor, merge, cleanup, ransongränser och felvägar mot nuvarande beteende.
- **E2:** Varje regel som avgör ett nästa steg har en enda testad källa, så att pollning och återupptagning inte kan ge olika svar på samma fakta.
  - **Verified by:** Kodinventeringen namnger varje regel och dess enda implementation; tester med samma faktaunderlag via varje ingång ger samma beslut och fallerar om en dubblerad beslutsväg införs.
- **E3:** `scripts/dispatcher-invoke.sh` minskar med minst 30 procent från 4 137 rader utan att komplexiteten bara flyttas till en annan stor shellfil.
  - **Verified by:** Radantalet är högst 2 895; det sammanlagda radantalet i `scripts/dispatcher-*.sh` minskar, flyttade regler ligger i testade moduler och relevanta fokus- och CI-tester är gröna.

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
