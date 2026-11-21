from dmx_docs import onenote

NS = 'xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote"'
HIER = f"""<one:Notebook {NS}><one:Section name="Pompes" ID="s1">
<one:Page ID="p1" name="A savoir" dateTime="2022-07-12T13:50:28.000Z" lastModifiedTime="2022-10-14T11:40:50.000Z" pageLevel="1"/>
<one:Page ID="p2" name="Détails" dateTime="2022-07-13T08:00:00.000Z" lastModifiedTime="2022-07-13T09:00:00.000Z" pageLevel="2"/>
<one:Page ID="p3" name="Vide" dateTime="2023-01-01T08:00:00.000Z" lastModifiedTime="2023-01-01T08:00:00.000Z" pageLevel="1"/>
</one:Section><one:Section name="Autre" ID="s2"/></one:Notebook>"""
PAGE = f"""<one:Page {NS} ID="p1" name="A savoir">
<one:Title><one:OE><one:T><![CDATA[A savoir]]></one:T></one:OE></one:Title>
<one:Outline><one:OEChildren>
<one:OE><one:T><![CDATA[Le débit d'air <span style='font-weight:bold'>s'additionne</span> &amp; les fuites aussi]]></one:T></one:OE>
<one:OE><one:Table><one:Row><one:Cell><one:OEChildren><one:OE><one:T><![CDATA[Pompe]]></one:T></one:OE></one:OEChildren></one:Cell>
<one:Cell><one:OEChildren><one:OE><one:T><![CDATA[610 l/min]]></one:T></one:OE></one:OEChildren></one:Cell></one:Row></one:Table></one:OE>
<one:OE><one:Image><one:OCRData><one:OCRText><![CDATA[Pilotage interne ou externe ?]]></one:OCRText></one:OCRData></one:Image></one:OE>
<one:OE><one:InsertedFile preferredName="Courbe pompe.pdf"/></one:OE>
</one:OEChildren></one:Outline></one:Page>"""


def test_section_pages_and_titles():
    pages = onenote.section_pages(HIER, "Pompes")
    assert [(p.id, p.name, p.level) for p in pages] == [("p1", "A savoir", 1), ("p2", "Détails", 2), ("p3", "Vide", 1)]
    assert onenote.page_titles(pages) == ["A savoir", "A savoir > Détails", "Vide"]
    assert onenote.section_pages(HIER, "Autre") == []


def test_page_text():
    assert onenote.page_text(PAGE, "A savoir", "2022-07-12T13:50:28.000Z", "2022-10-14T11:40:50.000Z") == (
        "A savoir\nCréée le 2022-07-12, modifiée le 2022-10-14\n"
        "Le débit d'air s'additionne & les fuites aussi\nPompe | 610 l/min\n"
        "Fichiers joints : Courbe pompe.pdf\n[Texte des images]\nPilotage interne ou externe ?")


def test_page_with_pictures_only_keeps_title_and_dates():     # Review Focus 3
    xml = f'<one:Page {NS} ID="p3" name="Vide"><one:Outline><one:OE><one:Image/></one:OE></one:Outline></one:Page>'
    assert onenote.page_text(xml, "Vide", "2023-01-01T08:00:00.000Z", "2023-01-01T08:00:00.000Z") == \
        "Vide\nCréée le 2023-01-01, modifiée le 2023-01-01"
