#!/usr/bin/env python3
"""
Simplifie les motifs <notAllowed/> d'un schema RELAX NG.

Pourquoi ce module existe. Un ODD qui n'importe qu'une partie de la TEI vide
des classes de modele : plus aucun membre de model.lLike si l'on n'encode pas
de vers, plus aucun de model.quoteLike si l'on ne cite pas. odd2relax rend
ces classes par <notAllowed/> -- le motif qui ne reconnait rien -- ce qui est
exact, mais laisse au processeur le soin de les reduire.

libxml2, sur lequel repose toute la validation du projet, ne le fait pas
utilement : la compilation du schema du projet tournait encore apres dix
minutes, et une version qui compilait rejetait des documents pourtant
conformes (une <zone> imbriquee dans une <zone>) parce que les modeles de
contenu contamines n'avaient pas ete reduits.

Les regles appliquees ici sont celles de la specification RELAX NG 1.0,
section 4.20. Elles ne changent pas la langue reconnue par le schema : elles
ecrivent autrement ce que le schema dit deja.

    choice(notAllowed, p)  = p           un choix perd sa branche impossible
    choice(notAllowed, ..) = notAllowed  s'il ne reste plus rien
    group(notAllowed, p)   = notAllowed  la sequence exige l'impossible
    interleave, oneOrMore, list, mixed   idem
    zeroOrMore(notAllowed) = empty       zero occurrence reste permis
    optional(notAllowed)   = empty
    element(notAllowed)    = notAllowed
    attribute(notAllowed)  = notAllowed
    except(notAllowed)     -- l'exception disparait

Et, propagation faite jusqu'au point fixe : une <define> qui se reduit a
notAllowed rend impossible toute <ref> qui la nomme.
"""

from lxml import etree

RNG = "http://relaxng.org/ns/structure/1.0"

# Un motif compose devient impossible des qu'un de ses enfants l'est :
# il faut TOUTES ses parties.
EXIGEANTS = {"group", "interleave", "oneOrMore", "list", "mixed",
             "element", "attribute"}
# Un motif repetable ou facultatif tombe a <empty/> : zero occurrence
# reste une occurrence permise.
FACULTATIFS = {"zeroOrMore", "optional"}


def _nom(el):
    return etree.QName(el).localname if isinstance(el.tag, str) else None


def _motifs(el):
    """Les enfants qui sont des motifs, hors documentation et name-class.

    <element name="x"> porte son nom en attribut ; <element><anyName/>…
    le porte en element. Une name-class n'est pas un motif et ne rend
    rien impossible."""
    classes_de_noms = {"name", "anyName", "nsName", "choice_name"}
    for enfant in el:
        nom = _nom(enfant)
        if nom is None or nom in ("param", "except"):
            continue
        if _nom(el) in ("element", "attribute") and nom in classes_de_noms:
            continue
        yield enfant


def _impossible(el, noms_impossibles):
    """*el* est-il un motif qui ne reconnait rien ?"""
    nom = _nom(el)
    if nom == "notAllowed":
        return True
    if nom == "ref":
        return el.get("name") in noms_impossibles
    return False


def _remplacer(el, par):
    """Remplace *el* par un motif nu nomme *par*, en place."""
    parent = el.getparent()
    neuf = etree.SubElement(parent, f"{{{RNG}}}{par}")
    parent.replace(el, neuf)
    return neuf


def _simplifier_motif(el, noms_impossibles):
    """Un passage ascendant sur *el*. Renvoie le nombre de reductions."""
    reductions = 0
    for enfant in list(el):
        if isinstance(enfant.tag, str):
            reductions += _simplifier_motif(enfant, noms_impossibles)

    nom = _nom(el)
    if nom is None or nom in ("grammar", "div"):
        return reductions

    # Une <define> ou un <start> portant plusieurs motifs vaut un <group>
    # (RELAX NG 1.0, 4.12) : un seul motif impossible les rend impossibles.
    # C'est la forme qu'odd2relax donne aux classes _sequenceRepeatable.
    if nom in ("define", "start"):
        deja_reduit = len(el) == 1 and _nom(el[0]) == "notAllowed"
        if not deja_reduit and any(_impossible(e, noms_impossibles)
                                   for e in _motifs(el)):
            for enfant in list(el):
                el.remove(enfant)
            etree.SubElement(el, f"{{{RNG}}}notAllowed")
            return reductions + 1
        return reductions

    # <except> qui ne retranche plus rien : l'exception disparait.
    if nom == "except":
        if all(_impossible(e, noms_impossibles) for e in _motifs(el)):
            el.getparent().remove(el)
            return reductions + 1
        return reductions

    enfants = list(_motifs(el))
    if not enfants:
        return reductions

    if nom == "choice":
        impossibles = [e for e in enfants if _impossible(e, noms_impossibles)]
        if impossibles:
            for e in impossibles:
                el.remove(e)
            reductions += len(impossibles)
            restants = list(_motifs(el))
            if not restants:
                _remplacer(el, "notAllowed")
            elif len(restants) == 1:
                # Un choix a une branche est cette branche.
                el.getparent().replace(el, restants[0])
            return reductions
    elif any(_impossible(e, noms_impossibles) for e in enfants):
        if nom in FACULTATIFS:
            _remplacer(el, "empty")
            return reductions + 1
        if nom in EXIGEANTS:
            _remplacer(el, "notAllowed")
            return reductions + 1

    return reductions + _simplifier_vides(el, nom)


def _simplifier_vides(el, nom):
    """Les regles sur <empty/> (RELAX NG 1.0, 4.19).

    Reduire les notAllowed en produit : zeroOrMore(notAllowed) devient
    empty, et un <zeroOrMore><empty/></zeroOrMore> laisse libxml2 rejeter
    des documents conformes. Le vide s'absorbe dans une sequence et dans
    une repetition ; il ne s'absorbe pas dans un choix, ou il porte le sens
    « ou rien », ni dans un element, ou il dit « sans contenu »."""
    enfants = list(_motifs(el))
    if not enfants:
        return 0
    vides = [e for e in enfants if _nom(e) == "empty"]
    if not vides:
        return 0

    # Dans un choix, <empty/> porte le sens « ou rien » : il ne s'absorbe
    # pas. Il ne disparait que si le choix ne propose plus que du vide.
    if nom == "choice":
        if len(enfants) > 1 and len(vides) == len(enfants):
            _remplacer(el, "empty")
            return 1
        return 0

    # Partout ailleurs, plusieurs enfants valent un group implicite : le
    # vide s'y absorbe. C'est le cas du <oneOrMore> a deux enfants
    # qu'odd2relax ecrit dans <listPerson>.
    if len(vides) < len(enfants):
        for e in vides:
            el.remove(e)
        restants = list(_motifs(el))
        if nom in ("group", "interleave") and len(restants) == 1:
            el.getparent().replace(el, restants[0])
        return len(vides)

    # Tout est vide : la sequence et la repetition se reduisent au vide ;
    # un element ou un attribut sans contenu, lui, reste ce qu'il est.
    if nom in ("group", "interleave", "zeroOrMore", "oneOrMore", "optional"):
        _remplacer(el, "empty")
        return 1

    return 0


def _noms_impossibles(grammaire):
    """Les noms de <define> dont TOUTES les definitions sont impossibles.

    odd2relax emet plusieurs <define combine="choice"> pour un meme nom ;
    le nom n'est impossible que si aucune de ses definitions ne reconnait
    quoi que ce soit."""
    par_nom = {}
    for define in grammaire.iter(f"{{{RNG}}}define"):
        par_nom.setdefault(define.get("name"), []).append(define)
    impossibles = set()
    for nom, definitions in par_nom.items():
        if all(len(d) == 1 and _nom(d[0]) == "notAllowed" for d in definitions):
            impossibles.add(nom)
    return impossibles, par_nom


def simplifier(grammaire):
    """Reduit les <notAllowed/> de *grammaire*, en place, jusqu'au point fixe.

    Args:
        grammaire (etree._Element | etree._ElementTree): la grammaire RELAX NG.

    Returns:
        int: le nombre de motifs elimines.
    """
    if hasattr(grammaire, "getroot"):
        grammaire = grammaire.getroot()

    total = 0
    while True:
        impossibles, par_nom = _noms_impossibles(grammaire)
        reductions = _simplifier_motif(grammaire, impossibles)

        # Une definition devenue impossible disparait, avec les references
        # qui la nomment -- lesquelles viennent d'etre reduites ci-dessus.
        for nom in impossibles:
            for define in par_nom[nom]:
                define.getparent().remove(define)
                reductions += 1
        # Une branche impossible d'un define combine tombe seule.
        for define in list(grammaire.iter(f"{{{RNG}}}define")):
            if len(define) == 1 and _nom(define[0]) == "notAllowed" \
                    and define.get("combine"):
                define.getparent().remove(define)
                reductions += 1

        total += reductions
        if reductions == 0:
            return total
