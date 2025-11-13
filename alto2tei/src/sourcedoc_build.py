from lxml import etree
from multiprocessing import Pool, cpu_count
from .order_files import Files
from .sourcedoc_attributes import Attributes
from .sourcedoc_elements import SurfaceTree, labels
from .constants import NS_ALTO


XML_PARSER = etree.XMLParser(huge_tree=True, recover=True)


def build_surface_fragment(args):
    document_name, filepath, num, segmonto_zones, segmonto_lines, config = args

    input_alto_root = etree.parse(str(filepath), parser=XML_PARSER).getroot()
    tags = labels(filepath)
    attributes = Attributes(document_name, num, input_alto_root, tags, config)
    surface_tree = SurfaceTree(document_name, num, input_alto_root)

    # surface isolée
    surface = etree.Element("surface", attributes.surface())
    skip_glyphs = bool(config.get("perf", {}).get("skip_glyphs"))

    # index des éléments par ID
    by_id = {el.get("ID"): el for el in input_alto_root.xpath('//*[@ID]')}

    # bloc zone
    textblocks = attributes.zones("PrintSpace", "TextBlock", segmonto_zones)

    for tb in textblocks:
        if not tb.id:
            continue

        textblock = surface_tree.zone1(surface, tb.attributes, tb.id, num)
        textlines = attributes.zones(f'TextBlock[@ID=\"{tb.id}\"]', "TextLine", segmonto_lines)

        for tl in textlines:
            if not tl.id:
                continue

            textline = surface_tree.zone2(textblock, tb.id, tl.attributes, tl.id, num)
            words_parts = []

            line_node = by_id.get(tl.id)
            if line_node is None:
                continue

            if skip_glyphs:
                for ch in line_node:
                    local = etree.QName(ch).localname
                    if local == "String":
                        c = ch.get("CONTENT")
                        if c:
                            words_parts.append(c)
                    elif local == "SP":
                        words_parts.append(" ")
                surface_tree.line(textline, tb.id, tl.id, 0, " ".join(words_parts).strip())
                continue

            first_string = None
            for ch in line_node:
                if etree.QName(ch).localname == "String":
                    first_string = ch
                    break

            if first_string is None:
                continue
            if first_string.get("CONTENT") and len(first_string.getchildren()) == 0:
                surface_tree.line(textline, tb.id, tl.id, 0, None)
                continue

            for ch in line_node:
                local = etree.QName(ch).localname
                if local == "SP":
                    textline_child_id = ch.get("ID")
                    if not textline_child_id:
                        continue
                    space_data = attributes.zones(
                        f'TextLine[@ID=\"{tl.id}\"]',
                        f'SP[@ID=\"{textline_child_id}\"]',
                        None)[0]
                    surface_tree.zone3(
                        textline, tb.id, tl.id,
                        space_data.attributes, space_data.id, num)


                elif local == "String":
                    textline_child_id = ch.get("ID")
                    if not textline_child_id:
                        continue
                    string_data = attributes.zones(
                        f'TextLine[@ID=\"{tl.id}\"]',
                        f'String[@ID=\"{textline_child_id}\"]',
                        None)[0]

                    string = surface_tree.zone3(
                        textline, tb.id, tl.id,
                        string_data.attributes, string_data.id, num)

                    string_el = by_id.get(textline_child_id)
                    if string_el is None:
                        continue

                    glyphs = string_el.findall("a:Glyph", namespaces=NS_ALTO)
                    if glyphs:
                        word = "".join([g.get("CONTENT") or "" for g in glyphs])
                        if word:
                            words_parts.append(word)

                        for g in glyphs:
                            gid = g.get("ID")
                            if not gid:
                                continue
                            glyph_data = attributes.zones(
                                f'String[@ID=\"{textline_child_id}\"]',
                                f'Glyph[@ID=\"{gid}\"]', None)[0]
                            glyph = surface_tree.zone4(
                                string, tb.id, tl.id,
                                textline_child_id,
                                glyph_data.attributes, gid, num)
                            surface_tree.car(
                                glyph, g, tb.id, tl.id,
                                textline_child_id, gid, num)

            surface_tree.line(textline, tb.id, tl.id, num, " ".join(words_parts).strip())

    return num, etree.tostring(surface, encoding="utf-8")



def sourcedoc(
    document_name,
    output_tei_root,
    filepath_list,
    tags,
    segmonto_zones,
    segmonto_lines,
    config,
    progress=None,
    parent_task_docs=None,
    parent_task_pages=None
):

    ordered_files = Files(document_name, filepath_list).order_files()
    sourceDoc = etree.SubElement(output_tei_root, "sourceDoc")
    total_pages = len(ordered_files)

    # préparation arguments
    jobs = [
        (document_name, f.filepath, f.num, segmonto_zones, segmonto_lines, config)
        for f in ordered_files
    ]

    # nombre de workers
    workers = min(cpu_count(), 8)

    results = {}

    with Pool(workers) as pool:
        for idx, (num, xml_bytes) in enumerate(pool.imap_unordered(build_surface_fragment, jobs), start=1):

            results[num] = xml_bytes

            if progress and parent_task_pages is not None:
                progress.update(
                    parent_task_pages,
                    description=f"[cyan]{document_name}[/cyan] page {idx}/{total_pages}",
                    advance=1
                )

    # réassemblage dans l’ordre
    for f in sorted(ordered_files, key=lambda x: x.num):
        frag = results.get(f.num)
        if frag:
            sourceDoc.append(etree.fromstring(frag))

    return output_tei_root
