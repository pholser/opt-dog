# Raw judging-program sources

Publicly posted judging programs, downloaded 2026-11-03. Untrusted input: parse only, never execute.

## Jack Bradshaw Dog Show Superintendents (`bradshaw/`)
Parsed (`ingest/bradshaw.py`, `manifest.csv`).
URL pattern: `https://www.jbradshaw.com/<id>/jp.pdf`
- 50.pdf — Sir Francis Drake KC
- 450.pdf — (452 was an identical copy; dropped)
- 490.pdf — Del Sur KC
- 603.pdf — KC of Palm Springs, Jan 3–4 2026
- 843.pdf — Tucson KC / Greater Sierra Vista KC, Nov 2025
- 937.pdf — KC of Palm Springs
- 943.pdf — KC of Palm Springs, Jan 4 2024

## Baray (`baray/`)
URL pattern: `https://baray-production-storage.s3.us-west-2.amazonaws.com/judging_program/<key>.pdf`
- 0IsweiQquNV4n5MuNiFsEKR8yAbJ31RpKPpm0jiV.pdf
- 4YJEhgiGYwDc6QnbhkxIq4CyIjMt1BWvdyPhom8a.pdf
- dc8qgX0HRX373VJ6SGb7aBCzxpieGK9I8POcHF3N.pdf — Memorial Day Cluster
- t0XbmdUPfPT4AAHS9zG16v8g8CzY6KGvy1aFBr9F.pdf

## Rau Dog Shows (`rau/`)
Parsed (`ingest/rau.py`, `manifest.csv`) except Brookhaven, whose groups are judged in the breed rings.
- https://raudogshows.com/wp-content/uploads/Brookhaven-2024-JP-2.pdf
- https://raudogshows.com/wp-content/uploads/Twin-Brooks-2024-JP-Mon.pdf
- https://www.raudogshows.com/wp-content/uploads/KC-Anne-Arundel-Carroll-KC-2024-JP-Thurs.pdf
- https://www.raudogshows.com/wp-content/uploads/KC-Anne-Arundel-Carroll-KC-2024-JP-Fri.pdf

## MB-F / InfoDog (`mbf/`)
Downloaded 2026-11-21. Parsed (`ingest/mbf.py`, `manifest.csv`). URL pattern: `https://pdf.infodog.com/jps/<n>/jpg<n>.pdf`
(the number is reused from year to year, so a URL serves the latest program).
- jpg1134.pdf — Furniture City KC, Winston-Salem NC, Sun Nov 8 2026: 690 dogs, 138 breeds/varieties; show hours 7 AM–7 PM
- jpg328.pdf — Ingham County KC, Lansing MI, Sun Nov 29 2026: 1040 dogs, 150 breeds/varieties; show hours 6 AM–10 PM

## Jack Onofrio Dog Shows (`onofrio/`)
Downloaded 2026-11-21; no parser yet. URL pattern: `https://www.onofrio.com/jp/<code>jp.pdf`, linked from
`https://www.onofrio.com/execpgm/fndindex?page=JP0000000000@@`; past years' programs are not kept there.
- xcnr126jp.pdf — Conroe KC (1) and (2), Conroe TX, Sat–Sun Nov 28–29 2026, with concurrent specialties;
  show hours 6 AM–8 PM
- xcob126jp.pdf — Council Bluffs KC (1) and (2), Council Bluffs IA, Sat–Sun Nov 21–22 2026 (672 dogs on
  one day), with specialties; show hours 6 AM–6 PM

## Misc (`misc/`)
- mid-kentucky-kc-2023-03-16-excerpt.pdf — pages 15–20 of Mid-Kentucky KC 2023 catalog (breed rings only; no group/BIS page). Recovered from this repo's history.
