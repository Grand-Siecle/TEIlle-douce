from pathlib import Path
from lxml import etree
from multiprocessing import Pool, cpu_count
from src.order_files import Files
from src.sourcedoc_attributes import Attributes
from src.sourcedoc_elements import SurfaceTree, labels
from src.teiheader_metadata.iiif_data import IIIFMapping

XML_PARSER = etree.XMLParser(huge_tree=True, recover=True)


def build_surface_fragment(args):
    """
    Construit un fragment <surface> pour une page ALTO.

    Args:
        args: tuple contenant (document_name, filepath, num, segmonto_zones,
              segmonto_lines, config, iiif_mapping_dict)
    """
    document_name, filepath, num, segmonto_zones, segmonto_lines, config, iiif_mapping_dict = args

    input_alto_root = etree.parse(str(filepath), parser=XML_PARSER).getroot()
    file_stem = Path(filepath).stem

    # Get type zones and lines mapping
    tags = labels(filepath)

    # iiif mapping
    if iiif_mapping_dict:
        iiif_mapping = IIIFMapping()
        iiif_mapping.mapping = iiif_mapping_dict
        surface_tree = SurfaceTree(document_name, file_stem, input_alto_root, iiif_mapping)
    else: # without iiif service mapping
        surface_tree = SurfaceTree(document_name, file_stem, input_alto_root)

    # <surface>
    attributes = Attributes(document_name, file_stem, input_alto_root, tags, config)
    surface = surface_tree.surface(attributes.surface())

    # index des éléments par ID
    by_id = {el.get("ID"): el for el in input_alto_root.xpath('//*[@ID]')}

    # bloc zone
    textblocks = attributes.zones("PrintSpace", "TextBlock", segmonto_zones)

    for tb in textblocks:
        if not tb.id:
            continue

        textblock = surface_tree.zone1(surface, tb.attributes, tb.id, num)
        textlines = attributes.zones(f'TextBlock[@ID="{tb.id}"]', "TextLine", segmonto_lines)
        line_count = 0

        for tl in textlines:
            if not tl.id:
                continue

            textline = surface_tree.zone2(textblock, tb.id, tl.attributes, tl.id, num)

            line_node = by_id.get(tl.id)
            if line_node is None:
                continue

            words_parts = []
            for ch in line_node:
                local = etree.QName(ch).localname
                if local == "String":
                    c = ch.get("CONTENT")
                    if c:
                        words_parts.append(c)
                elif local == "SP":
                    words_parts.append(" ")
            line_count += 1
            surface_tree.line(textline, tb.id, tl.id, line_count, "".join(words_parts).strip())

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
        parent_task_pages=None,
        iiif_mapping=None
):
    """
    Construit le <sourceDoc> en parallélisant le traitement des pages.

    Args:
        ...
        iiif_mapping: instance de IIIFMapping (optionnel)
    """

    ordered_files = Files(document_name, filepath_list).order_files()
    sourceDoc = etree.SubElement(output_tei_root, "sourceDoc")
    total_pages = len(ordered_files)

    iiif_mapping_dict = None
    if iiif_mapping and iiif_mapping.has_mapping():
        iiif_mapping_dict = iiif_mapping.mapping.copy()

    # préparation arguments
    jobs = [
        (document_name, f.filepath, f.num, segmonto_zones, segmonto_lines, config, iiif_mapping_dict)
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

    for f in sorted(ordered_files, key=lambda x: x.num):
        frag = results.get(f.num)
        if frag:
            sourceDoc.append(etree.fromstring(frag))

    return output_tei_root