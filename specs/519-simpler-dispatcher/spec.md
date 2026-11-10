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

### Beteendeneutral förenkling — 2026-11-10

**Chosen:** Epicet samlar dubbla beslutsvägar och gör dem testbara utan att ändra vilket arbete HPW utför, när det utförs eller vilka grindar som måste passera. Befintliga fel- och stoppvägar ingår i jämförelsen.

**Rejected:** Att samtidigt ändra schemaläggning, granskningskrav eller mergepolicy — det skulle göra det svårt att visa att förenklingen bevarar säkerheten.

### Bygg vidare på ADR-014 — 2026-11-10

**Chosen:** Nya eller omskrivna beslutsregler ligger i testbar Python-kod enligt ADR-014; Bash behåller körordning, sessionsstart och utgångskoder. Förenklingen görs stegvis ovanpå de delade byggblock som redan är beslutade.

**Rejected:** En total omskrivning av dispatchern i ett steg — den skulle ge ett stort ändringsspann och göra beteendeparitet svår att kontrollera.

### Mätbar minskning och eget Epic — 2026-11-10

**Chosen:** `scripts/dispatcher-invoke.sh` ska bli minst 30 procent kortare än baslinjen 4 137 rader, och komplexiteten får inte bara flyttas till en annan stor shellfil. Epic #519 har inget hårt beroende till Codex-Epic #518 och inför inte Codex-funktioner.

**Rejected:** Ett rent radmål utan krav på testade beslutsregler eller total minskning av shellkod — det skulle gå att uppfylla genom att bara flytta kod. Att slå ihop #518 och #519 — det skulle blanda ny funktion med beteendeneutral förenkling.


## Checked Against

- ADR-014 och Epic #418, Feature #368 — de verifierade GitHub-skrivningarna, ägarbundna markörerna och det validerade sparade tillståndet är redan levererade. Detta Epic använder dem och följer ADR:ns uppdelning: Python för nya eller omskrivna beslutsregler, Bash för körordning och sessionsstart.
- ADR-002 och ADR-012 — GitHub Actions som Claude-runner och dess autentisering ändras inte av detta Epic.
- ADR-007 och `docs/MERGE-POLICY.md` — oberoende granskning på aktuell head, gröna tester, olösta trådar och färsk mergekontroll förblir grindar.
- ADR-010 — boardstatus är en härledd vy; förenklingen behåller GitHub-fakta som källa för beslut.
- Epic #160 och #306 — deras öppna dispatcher- och förfiningsarbete kan beröra samma filer. Samordna ordningen och återbasera vid behov, men gör dem inte till hårda beroenden och ändra inte deras beslut.
- Epic #518 — Codex som ny exekverare ägs där. Detta Epic förenklar dagens beteende oberoende av om #518 är levererat.
- `docs/DISPATCHER-CONFIG.md` och `docs/ADAPTERS.md` — uppdateras för den nya kodfördelningen, utan att ändra de dokumenterade workflowkontrakten.

## Delegated to the Tech Lead

- Inventera dubbla beslutsvägar och välj ordning, modulgränser och små refaktoreringssteg som bevarar beteendet.
- Välj regressionsmatris, testfixturer och mätmetod för radantal och samlat shellomfång.
- Samordna filändringar med Epic #160 och #306 samt de redan levererade byggblocken i ADR-014.
- För inte in nya workflowregler, Codex-funktioner, ändrad autentisering, ändrad granskningspolicy eller försvagade mergegrindar utan ett nytt PO-beslut.

## Dependencies

- Inga hårda beroenden. ADR-014:s byggblock #432, #433 och #434 är levererade.
- Samordning med de öppna Epicen #160 och #306 eftersom de kan ändra samma dispatcherfiler.
- Inget hårt beroende till Epic #518.

## Open Questions

<…>
