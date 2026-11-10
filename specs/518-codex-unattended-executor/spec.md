# Epic #518: Codex as an unattended executor

**Epic issue:** #518
**Milestone:** M6 Autonomous Execution

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

- **E1:** Minst två riktiga, berättigade stories förfinas och implementeras av Codex utan att PO startar modellsessionerna.
  - **Verified by:** Ärendenas och PR:ernas tidslinjer visar Codex-uppgifternas starter, förfiningsbevis, commits, testresultat och PR-överlämning, utan PO-kommandon mellan tilldelning och PR.
- **E2:** Codex-PR:er granskas oberoende av Claude på aktuell head-SHA, och blockerande fynd leder till en avgränsad Codex-fixrunda innan PR:en kan mergas.
  - **Verified by:** Minst en av bevis-PR:erna har Claude-granskning på sin aktuella head; ett kontrollerat fynd i ett test eller en verklig PR visar återgång till Codex, ny testkörning och förnyad granskning före merge.
- **E3:** Ett avbrott, en avstängd Codex-dator eller en uppnådd ransongräns lämnar arbetet i ett väntande, återupptagbart tillstånd utan dubbelstart, förlorade commits eller automatisk flytt till Claude.
  - **Verified by:** Ett avbrutet bevisarbete återupptas efter att datorn eller appen är tillbaka; deterministiska tester täcker ransongräns, upprepad körning och förlorat svar efter en GitHub-skrivning.
- **E4:** Codex-arbetet följer samma tester, PR-bevis, överlämningskrav och mergegrindar som Claude-arbetet och ger ingen separat API-debitering.
  - **Verified by:** Fokus- och CI-testerna är gröna på bevis-PR:ernas aktuella head-SHA, överlämningsbevisen finns på GitHub, mergegrindarna passerar oförändrade och PO kontrollerar att ingen OpenAI API-förbrukning uppstått under bevisperioden.

## Decisions

### Plus-abonnemanget och körplatsen — 2026-11-10

**Chosen:** Codex körs obemannat genom schemalagda uppgifter i den inloggade Codex-appen på PO:s dator. Datorn och appen får behöva vara igång. Inga OpenAI API-nycklar, köpta API-token eller Business-/Enterprise-abonnemang används för modellkörningen. Om denna väg inte kan uppfylla säkerhets- och beviskraven stoppar arbetet för ett nytt PO-beslut.

**Rejected:** Att köra Codex på HPW:s GitHub Actions-runner med en kopierad interaktiv inloggning — det är inte en dokumenterad Plus-autentisering för CI. En separat debiterad API-nyckel eller ett nytt Business-/Enterprise-abonnemang — de bryter mot målet att använda befintliga abonnemang utan separat debitering.

### Vänta och återuppta — 2026-11-10

**Chosen:** När datorn, appen eller Codex-ransonen hindrar arbete väntar Codex-tilldelade stories och återupptas med beständigt GitHub-tillstånd. Ingen automatisk överflyttning till Claude sker.

**Rejected:** Automatisk överflyttning till Claude — den skulle förbruka granskarreserven och ändra det tidigare beslutet i Epic #160 om ingen leverantörsfailover.

### Oberoende granskning och reserv — 2026-11-10

**Chosen:** Claude granskar Codex-PR:er på aktuell head-SHA enligt befintlig review- och mergepolicy. Codex lämnar samma test- och PR-bevis som Claude. Codex-arbetaren startar inte under en PO-reserv på 10 procent av femtimmarsgränsen eller 20 procent av veckogränsen; värdena kan ändras utan kodändring. Granskningskapacitet hos Claude skyddas också innan nytt Codex-arbete tas in.

**Rejected:** Codex granskar sitt eget arbete eller fortsätter starta arbete när ransongränserna gör full leverans omöjlig — det skulle försvaga oberoendet eller skapa en växande granskningskö.

### Separata Epic och ADR — 2026-11-10

**Chosen:** Codex-exekveraren kan levereras före den bredare förenklingen i Epic #519. En egen ADR-ändring av ADR-012 beslutar hur den lokala Codex-vägen kompletterar Claude-sessionerna i GitHub Actions innan implementation.

**Rejected:** Ett gemensamt Epic eller ett hårt beroende till #519 — det skulle hålla tillbaka Codex-piloten tills en bredare beteendeneutral ombyggnad är klar.


## Checked Against

<…>

## Delegated to the Tech Lead

<…>

## Dependencies

<…>

## Open Questions

<…>
