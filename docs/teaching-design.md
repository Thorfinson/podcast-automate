# Recherche in eine nachvollziehbare Lehrfolge umsetzen

Diese Arbeitsweise ist für neue `pla script`-Läufe implementiert. Auf den Serienplan folgen ein eigener Lehrplan mit Quellenprüfung, ein Fachentwurf, ein eigener Dialog-Polishing-Schritt mit Vorher-/Nachher-Vergleich sowie getrennte Quellen-, Lese- und Lehrprüfungen. Sie gilt für alle Projektthemen.

Ein Quellenverzeichnis und ein korrektes Dossier reichen nicht aus, um eine verständliche Lehrfolge zu erhalten. Zwischen Recherche und Dialog benötigt das Projekt eine ausgearbeitete Unterrichtsplanung. Der aktuelle Pilotentwurf liegt lokal unter `projects/windows-pilot/research/redesign/teaching_plan.md`.

## Was die Anwendung erzwingt

1. Die Recherche sucht auch nach den Grundlagen, die das gewählte Publikum vor den Fachquellen benötigt.
2. Die Stufe `teaching` entwickelt pro Folge Lernziele mit Prüffragen, Begriffsabhängigkeiten, Szenenübergänge, ein ausgearbeitetes Beispiel und eine begründete Synthese. Ein zusätzlicher Modellaufruf prüft diesen Entwurf gegen die Quellen.
3. Fehlende notwendige Belege erzeugen `runs/<run_id>/teaching/<episode_id>/research_needed.md` und blockieren das Schreiben. Der aktuelle Befehl führt keine automatische Nachrecherche aus. Die Fragen müssen im Rechercheauftrag ergänzt und mit einem neuen Recherchelauf bearbeitet werden. Ein bloßes `resume` beseitigt die Lücke nicht.
4. Die Stufe `polishing` überarbeitet den Entwurf für gesprochene Sprache und die Rollen Experte/Gesprächspartnerin. Ein zusätzlicher Vergleich prüft Bedeutung, Vollständigkeit, Rollen und Sprechbarkeit mit Textbelegen aus dem Entwurf und der neuen Fassung. Erfundenes Wissen oder verlorene Erklärungsschritte dürfen nicht als sprachliche Verbesserung durchgehen. [Ablauf und Ergebnisdateien](scripts.md#eigener-dialog-polishing-schritt).
5. Nach der Quellenprüfung beantwortet ein frischer Modellaufruf die Lernfragen anhand des überarbeiteten Dialogs. Dieser Aufruf bekommt weder Musterantworten noch Dossier oder Lehrplan. Eine zusätzliche redaktionelle Prüfung sieht ebenfalls nur Zielgruppe, Anspruch und gesprochenen Text. Sie prüft insbesondere den tatsächlichen Einstieg, notwendige Übergänge und inhaltliche Reaktionen der Sprecher aufeinander. Ihr negatives Urteil blockiert auch dann, wenn die anderen Modelle positiv urteilen.
6. Ein weiterer Aufruf prüft Einstieg, Aufbau, Beispiel, Synthese, Dialog, Tiefe, Hörverständlichkeit und jedes Lernziel. Ein bestandenes Kriterium benötigt wörtliche Belege aus vorhandenen Sprechersegmenten. Jede vom Leser gemeldete Lücke muss ausdrücklich als erforderlich oder außerhalb des Lernziels eingeordnet und diese Entscheidung begründet werden. Fehlende Kriterien, erfundene Belege, übergangene Lücken und fehlende erforderliche Erklärungen erlauben keinen Export.

Der Lehrplan und die Polishing-Fassung dürfen jeweils bis zu zweimal, der Dialog im abschließenden Review bis zu dreimal repariert werden. Die Grenzen und bereits fertigen Vergleiche und Leseprüfungen bleiben bei Wiederaufnahme erhalten. Alle Modellaufrufe zählen zum Budget des Skriptlaufs. Bei einem ausgeschöpften Budget oder Kontingent bleiben die Ergebnisse gespeichert.

`episodes/<episode_id>/teaching_plan.md` macht den Lehrplan lesbar. Der Detailbericht in `reports/script_quality.yaml` enthält die Leserantworten, belegten Einzelurteile und weiterhin `human_learning_validated: false`. Ein simuliertes Leseverständnis ist keine Messung an echten Hörern.

Die [Regressionstests mit echten Modellaufrufen](../evals/teaching_quality/README.md) verwenden dieselben Prüffunktionen wie die Produktion. Erwartete Urteile werden den Prüfern nicht mitgeteilt. Sie ergänzen die automatisierten Tests zu fehlenden Grundlagen, falschen Zitaten, Wiederaufnahme und Audiofreigabe.

Die [Verifikation vom 13.09.2026](quality-verification.md) dokumentiert sowohl die aktuellen Ergebnisse als auch die Fehlurteile früherer Prüfversionen.

## Bestehende Projekte

Vorhandene abgeschlossene Recherchen können als Quelle für einen neuen Skriptlauf dienen. Frühere Skriptläufe bekommen die neue Qualitätsprüfung nicht nachträglich als bestanden eingetragen. Nach dem Versionswechsel benötigen sie einen neuen `pla script`-Lauf; ein altes `resume` meldet geänderte Skripteingaben. Ein bereits gestarteter Audiojob bleibt an seinen bisherigen Text gebunden.

`--revise` behält den bisherigen Serienplan bei und erstellt den Lehrplan sowie die Textprüfungen neu. Für eine grundlegend andere Reihenfolge wird ein regulärer neuer `pla script`-Aufruf verwendet. Keine dieser Prüfungen ersetzt die bereits vereinbarte Leseprüfung vor neuem Audio.

## Vom Lernziel zur Recherche

Zuerst festlegen, was der Hörer nach der Folge erklären, vorhersagen, vergleichen oder an einem neuen Fall entscheiden können soll. Danach die dafür erforderlichen Voraussetzungen erfassen. Erst daraus folgen die Fragen an die Literatur. Das [Eberly Center](https://www.cmu.edu/teaching/designteach/design/learningobjectives.html) beschreibt diese Abstimmung von Zielen, Aufgaben und Unterricht.

Eine Quellenstelle kann fachlich passend und didaktisch zu fortgeschritten sein. Deshalb zusätzlich fragen, welche Begriffe, Darstellungen oder früheren Lektionen die Quelle voraussetzt. Solche Voraussetzungen sind Rechercheaufgaben, wenn der Hörer sie noch nicht mitbringt.

## Ein Lehrbaustein pro zusammenhängendem Gedankenschritt

Für jeden Baustein schriftlich beantworten:

1. Welche Frage ist nach dem bisherigen Verlauf offen?
2. Was kann der Hörer zu diesem Zeitpunkt bereits erklären?
3. Welches konkrete Beispiel oder welcher Vergleich macht die neue Schwierigkeit sichtbar?
4. Welche Schritte erklären den Mechanismus, und warum hilft jeder davon?
5. Welche verständliche, aber falsche Schlussfolgerung könnte entstehen?
6. Welche neue Folgerung ergibt sich aus diesem und früheren Bausteinen?
7. Welche Quellen stützen die Aussagen, und wo beginnt eine eigene Konstruktion?

Die Bausteine werden vor der Ausformulierung geprüft. Eine fehlende Verbindung wird durch eine Erklärung oder zusätzliche Recherche geschlossen. Eine Überleitung wie „Damit kommen wir zu …“ allein erfüllt diese Aufgabe nicht.

## Drei redaktionelle Prüfungen

| Perspektive | Prüffrage | Erwarteter Nachweis im Entwurf |
| --- | --- | --- |
| Erzählung | Warum will der Hörer gerade jetzt die nächste Antwort wissen? | Der vorangehende Lösungsversuch hat eine konkrete neue Schwierigkeit sichtbar gemacht. |
| Fachliche Lehre | Welche zusätzliche Denkleistung ermöglicht dieser Abschnitt? | Eine begründete Vorhersage, Rechnung, Unterscheidung oder Übertragung. |
| Lernbegleitung | Wo könnte ein Neuling den Faden verlieren oder eine falsche Regel bilden? | Eine vorbereitete Voraussetzung, ein erklärter Übergang oder ein gezielter Gegenfall. |

Diese Rollen bezeichnen Prüfperspektiven; sie behaupten keine Beteiligung externer Experten. Ein Modellreview muss konkrete Passagen und fehlende Schritte nennen. Das Fehlen formaler Fehler ist kein Nachweis, dass diese Prüfungen bestanden sind.

## Beispiele, Dialog und Synthese

Ein wiederkehrendes Hauptbeispiel schafft einen stabilen Bezug. Ein zweites Beispiel bekommt eine bestimmte Aufgabe: eine Grenze zeigen oder prüfen, ob der Gedanke übertragen werden kann. Neue Beispiele dürfen nicht stillschweigend zugleich die Aufgabe, die veränderlichen Größen und das Lernziel austauschen.

Der zweite Sprecher stellt begründete Einwände, trifft eine überprüfbare Vorhersage oder entwickelt eine Folgerung. Er muss nicht ständig Unwissen vorspielen. Sprecherwechsel folgen der Erklärung; feste Wortzahlen pro Beitrag sind kein Qualitätsziel. Längere zusammenhängende Monologe sind willkommen. Ein technischer Schnitt in mehrere Audiodateien verlangt keinen Sprecherwechsel; eine Grenze von 30–90 Sekunden pro Beitrag wird nicht vorgegeben. Das Gespräch soll durch inhaltliche Reaktionen entstehen, ohne mechanische Abwechslung oder erzwungene Unterbrechungen.

Ausgearbeitete Beispiele werden mit kurzen Gelegenheiten zum eigenen Erklären verbunden. Dafür empfiehlt der [IES-Leitfaden](https://ies.ed.gov/ncee/wwc/PracticeGuide/1) unter anderem erklärende Fragen und den Wechsel zwischen vorgeführter Lösung und eigenständiger Aufgabe. Im Podcast können daraus ein ernsthafter Einwand und eine kurze Denkpause werden; diese Anpassung muss sich in der Lese- und Hörprüfung bewähren.

Eine Synthese benennt ihre Ausgangspunkte und führt sichtbar zu einer zusätzlichen Einsicht. Eine Wiederholung der Kapitelüberschriften genügt nicht. Quellenbefund, eigene Ableitung, konstruiertes Beispiel und offene Vermutung werden in den redaktionellen Notizen getrennt. Im gesprochenen Text erscheinen die Grenzen dort, wo sie das Verständnis tatsächlich verändern.

## Umfang und Übernahme

Universitäre Tiefe verlangt begründete Zusammenhänge, überprüfbare Schritte und Grenzen. Eine hohe Dichte an Begriffen belegt das ebenso wenig wie ein langer Text. Der Umfang ergibt sich aus den erforderlichen Lernschritten; bei einer Dateigrenze wird an einer beantworteten Teilfrage geteilt.

Das neue Skript wird zuerst lesbar bereitgestellt. Seine spätere Vertonung folgt der bestehenden Nutzeranforderung zur ausdrücklichen Freigabe dieses Textstands. Ein bereits laufender Audioauftrag darf nicht durch Änderungen an seinen gespeicherten Eingaben auf eine neue Fassung umgestellt werden.
