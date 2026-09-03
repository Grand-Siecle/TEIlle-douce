# Tests de scripts/rng_simplify.py
#
# odd2relax rend une classe TEI videe par l'elagage sous la forme d'un motif
# <notAllowed/>. C'est correct au sens de la spec RELAX NG -- le motif ne
# reconnait rien -- mais libxml2 ne simplifie pas ces motifs : sur le schema
# du projet, sa compilation tournait encore apres dix minutes, et les
# modeles de contenu qui en dependent rejetaient des documents valides.
#
# La spec (RELAX NG 1.0, section 4.20) dit exactement comment reduire ces
# motifs. Ce module applique ces regles avant que lxml ne voie le schema.
#
# Run: venv/bin/python -m pytest tests/test_rng_simplify.py -q
from lxml import etree

from scripts.rng_simplify import simplifier

RNG = "http://relaxng.org/ns/structure/1.0"


def grammaire(corps):
    """Une grammaire RELAX NG minimale autour de *corps*."""
    return etree.fromstring(
        f'<grammar xmlns="{RNG}"><start>{corps}</start></grammar>'.encode()
    )


def rendu(el):
    """Serialisation sans namespace, pour des assertions lisibles."""
    texte = etree.tostring(el, encoding="unicode")
    return texte.replace(f' xmlns="{RNG}"', "").replace("\n", "").strip()


def start(arbre):
    return arbre.find(f"{{{RNG}}}start")[0]


# -----------------------------------------------------------
# Les regles, une par une (RELAX NG 4.20)
# -----------------------------------------------------------

def test_un_choix_perd_la_branche_impossible():
    """choice(notAllowed, p) = p : une alternative qui ne reconnait rien
    n'enleve rien aux autres."""
    arbre = grammaire("<choice><notAllowed/><text/></choice>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<text/>"


def test_un_choix_entierement_impossible_le_devient():
    arbre = grammaire("<choice><notAllowed/><notAllowed/></choice>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<notAllowed/>"


def test_une_sequence_contaminee_devient_impossible():
    """group(notAllowed, p) = notAllowed : la sequence exige la partie
    impossible, donc elle est impossible."""
    arbre = grammaire("<group><notAllowed/><text/></group>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<notAllowed/>"


def test_une_repetition_facultative_devient_vide():
    """zeroOrMore(notAllowed) = empty : zero occurrence reste permis."""
    arbre = grammaire("<zeroOrMore><notAllowed/></zeroOrMore>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<empty/>"


def test_un_optionnel_impossible_devient_vide():
    arbre = grammaire("<optional><notAllowed/></optional>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<empty/>"


def test_une_repetition_obligatoire_reste_impossible():
    """oneOrMore(notAllowed) = notAllowed : il en faut au moins une."""
    arbre = grammaire("<oneOrMore><notAllowed/></oneOrMore>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<notAllowed/>"


def test_un_element_au_contenu_impossible_devient_impossible():
    arbre = grammaire('<element name="x"><notAllowed/></element>')
    simplifier(arbre)
    assert rendu(start(arbre)) == "<notAllowed/>"


def test_un_attribut_a_la_valeur_impossible_devient_impossible():
    arbre = grammaire('<attribute name="a"><notAllowed/></attribute>')
    simplifier(arbre)
    assert rendu(start(arbre)) == "<notAllowed/>"


# -----------------------------------------------------------
# Propagation a travers les definitions
# -----------------------------------------------------------

def test_une_definition_impossible_contamine_ses_references():
    """Une classe TEI videe par l'elagage est une <define> qui se reduit a
    notAllowed ; toutes les <ref> qui la nomment doivent suivre."""
    arbre = etree.fromstring(f'''<grammar xmlns="{RNG}">
      <start><choice><ref name="vide"/><text/></choice></start>
      <define name="vide"><notAllowed/></define>
    </grammar>'''.encode())
    simplifier(arbre)
    assert rendu(start(arbre)) == "<text/>"
    assert arbre.find(f'{{{RNG}}}define[@name="vide"]') is None, \
        "la definition impossible doit disparaitre avec ses references"


def test_la_propagation_va_jusqu_au_point_fixe():
    """a -> b -> c, ou c est impossible : les trois doivent tomber, ce
    qu'un seul passage ne donnerait pas."""
    arbre = etree.fromstring(f'''<grammar xmlns="{RNG}">
      <start><choice><ref name="a"/><text/></choice></start>
      <define name="a"><group><ref name="b"/><text/></group></define>
      <define name="b"><oneOrMore><ref name="c"/></oneOrMore></define>
      <define name="c"><notAllowed/></define>
    </grammar>'''.encode())
    simplifier(arbre)
    assert rendu(start(arbre)) == "<text/>"
    for nom in ("a", "b", "c"):
        assert arbre.find(f'{{{RNG}}}define[@name="{nom}"]') is None, nom


def test_des_definitions_combinees_ne_tombent_que_toutes_ensemble():
    """odd2relax emet plusieurs <define combine="choice"> pour un meme nom :
    le nom n'est impossible que si TOUTES le sont."""
    arbre = etree.fromstring(f'''<grammar xmlns="{RNG}">
      <start><ref name="x"/></start>
      <define name="x" combine="choice"><notAllowed/></define>
      <define name="x" combine="choice"><text/></define>
    </grammar>'''.encode())
    simplifier(arbre)
    restantes = arbre.findall(f'{{{RNG}}}define[@name="x"]')
    assert len(restantes) == 1, "la branche impossible seule devait tomber"
    assert rendu(restantes[0][0]) == "<text/>"


def test_un_motif_sans_notallowed_est_laisse_intact():
    corps = '<group><element name="x"><text/></element><zeroOrMore><text/></zeroOrMore></group>'
    arbre = grammaire(corps)
    avant = rendu(start(arbre))
    simplifier(arbre)
    assert rendu(start(arbre)) == avant


def test_simplifier_dit_combien_de_motifs_il_a_elimines():
    arbre = grammaire("<choice><notAllowed/><text/></choice>")
    assert simplifier(arbre) == 1
    assert simplifier(arbre) == 0, "un arbre deja simplifie ne bouge plus"


def test_une_definition_a_plusieurs_motifs_vaut_une_sequence():
    """La spec dit qu'une <define> portant plusieurs motifs equivaut a un
    <group> : si l'un d'eux est impossible, la definition l'est. C'est la
    forme qu'odd2relax donne aux classes _sequenceRepeatable de la TEI."""
    arbre = etree.fromstring(f'''<grammar xmlns="{RNG}">
      <start><choice><ref name="x"/><text/></choice></start>
      <define name="x"><text/><notAllowed/></define>
    </grammar>'''.encode())
    simplifier(arbre)
    assert rendu(start(arbre)) == "<text/>"
    assert arbre.find(f'{{{RNG}}}define[@name="x"]') is None


def test_une_definition_deja_reduite_ne_compte_plus_de_reduction():
    """Le point fixe s'atteint par un compteur qui retombe a zero : une
    <define> deja reduite a notAllowed ne doit plus etre comptee, sinon la
    boucle de simplification ne s'arrete jamais."""
    arbre = etree.fromstring(f'''<grammar xmlns="{RNG}">
      <start><text/></start>
      <define name="x"><notAllowed/></define>
    </grammar>'''.encode())
    simplifier(arbre)
    assert simplifier(arbre) == 0


# -----------------------------------------------------------
# Les regles sur <empty/> (RELAX NG 4.19)
#
# Reduire les notAllowed produit des <empty/>, qu'il faut reduire a leur
# tour : libxml2 rejette des documents conformes devant un
# <zeroOrMore><empty/></zeroOrMore>, motif sans fin qu'il ne sait pas
# ramener a <empty/>.
# -----------------------------------------------------------

def test_une_sequence_perd_ses_parties_vides():
    """group(empty, p) = p."""
    arbre = grammaire("<group><empty/><text/></group>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<text/>"


def test_une_sequence_toute_vide_est_vide():
    arbre = grammaire("<group><empty/><empty/></group>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<empty/>"


def test_une_repetition_de_vide_est_vide():
    """zeroOrMore(empty) = empty : repeter le vide ne donne que du vide,
    et le motif repetitif est ce qui egare libxml2."""
    arbre = grammaire("<zeroOrMore><empty/></zeroOrMore>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<empty/>"


def test_une_repetition_obligatoire_de_vide_est_vide():
    arbre = grammaire("<oneOrMore><empty/></oneOrMore>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<empty/>"


def test_un_optionnel_vide_est_vide():
    arbre = grammaire("<optional><empty/></optional>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<empty/>"


def test_un_entrelacement_perd_ses_parties_vides():
    arbre = grammaire("<interleave><empty/><text/></interleave>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<text/>"


def test_un_choix_entre_vides_est_vide():
    arbre = grammaire("<choice><empty/><empty/></choice>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<empty/>"


def test_un_choix_garde_sa_branche_vide_significative():
    """choice(empty, p) n'est PAS p : c'est « p ou rien ». Le vide y porte
    du sens, contrairement a une sequence."""
    arbre = grammaire("<choice><empty/><text/></choice>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<choice><empty/><text/></choice>"


def test_un_element_au_contenu_vide_le_reste():
    """element(empty) est un element sans contenu : parfaitement legitime."""
    arbre = grammaire('<element name="x"><empty/></element>')
    simplifier(arbre)
    assert rendu(start(arbre)) == '<element name="x"><empty/></element>'


def test_le_vide_s_absorbe_dans_un_groupe_implicite():
    """Un motif portant plusieurs enfants est un group implicite (RELAX NG
    1.0, 4.12) : le vide s'y absorbe comme dans un <group> ecrit. C'est la
    forme qu'odd2relax donne au contenu de <listPerson>, et l'avoir laissee
    faisait rejeter les <person> qu'elle contient."""
    arbre = grammaire("<oneOrMore><text/><empty/></oneOrMore>")
    simplifier(arbre)
    assert rendu(start(arbre)) == "<oneOrMore><text/></oneOrMore>"


def test_le_vide_s_absorbe_dans_le_contenu_d_un_element_deja_pourvu():
    arbre = grammaire('<element name="x"><text/><empty/></element>')
    simplifier(arbre)
    assert rendu(start(arbre)) == '<element name="x"><text/></element>'
