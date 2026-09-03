<?xml version="1.0" encoding="UTF-8"?>
<schema xmlns="http://purl.oclc.org/dsdl/schematron" queryBinding="xslt2">
   <title>ISO Schematron rules</title>
   <!-- This file generated 2026-09-03T09:07:17Z by 'extract-isosch.xsl'. -->
   <!-- ********************* -->
   <!-- namespaces, declared: -->
   <!-- ********************* -->
   <ns prefix="tei" uri="http://www.tei-c.org/ns/1.0"/>
   <ns prefix="xs" uri="http://www.w3.org/2001/XMLSchema"/>
   <ns prefix="rng" uri="http://relaxng.org/ns/structure/1.0"/>
   <ns prefix="rna" uri="http://relaxng.org/ns/compatibility/annotations/1.0"/>
   <ns prefix="sch" uri="http://purl.oclc.org/dsdl/schematron"/>
   <ns prefix="sch1x" uri="http://www.ascc.net/xml/schematron"/>
   <!-- ******************************************************* -->
   <!-- constraints in en, und, mul, zxx, of which there are 50 -->
   <!-- ******************************************************* -->
   <pattern id="schematron-constraint-CMC_generatedBy_within_post-1">
      <rule context="tei:*[@generatedBy]">
         <assert test="ancestor-or-self::tei:post">The @generatedBy attribute is for use within a &lt;post&gt; element.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-att-datable-w3c-when-2">
      <rule context="tei:*[@when]">
         <report test="@notBefore|@notAfter|@from|@to" role="nonfatal">The @when attribute cannot be used with any other att.datable.w3c attributes.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-att-datable-w3c-from-3">
      <rule context="tei:*[@from]">
         <report test="@notBefore" role="nonfatal">The @from and @notBefore attributes cannot be used together.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-att-datable-w3c-to-4">
      <rule context="tei:*[@to]">
         <report test="@notAfter" role="nonfatal">The @to and @notAfter attributes cannot be used together.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-only_1_ODD_source-5">
      <rule context="tei:*[@source]">
         <let name="srcs" value="tokenize( normalize-space(@source),' ')"/>
         <report test="(   self::tei:classRef                                 | self::tei:dataRef                                 | self::tei:elementRef                                 | self::tei:macroRef                                 | self::tei:moduleRef                                 | self::tei:schemaSpec )                                   and                                   $srcs[2]"> When used on a schema description element (like &lt;<value-of select="name(.)"/>&gt;), the @source attribute should have only 1 value. (This one has <value-of select="count($srcs)"/>.)</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-targetLang-6">
      <rule context="tei:*[not(self::tei:schemaSpec)][@targetLang]">
         <assert test="@target">@targetLang should only be used on &lt;<name/>&gt; if @target is specified.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-spanTo-points-to-following-7">
      <rule context="tei:*[ starts-with( @spanTo, '#') ]">
         <assert test="id( substring( @spanTo, 2 ) ) &gt;&gt; ."> The element indicated by @spanTo (<value-of select="@spanTo"/>) must follow the current &lt;<name/>&gt; element.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-subtypeTyped-8">
      <rule context="tei:*[@subtype]">
         <assert test="@type"> The &lt;<name/>&gt; element should not be categorized in detail with @subtype unless also categorized in general with @type.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-calendar_attr_on_empty_element-9">
      <rule context="tei:*[@calendar]">
         <assert test="string-length( normalize-space(.) ) gt 0"> @calendar indicates one or more systems or calendars to which the date represented by the content of this element belongs, but this &lt;<name/>&gt; element has no textual content.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-abstractModel-structure-p-in-ab-or-p-10">
      <rule context="tei:p">
         <report test="(ancestor::tei:ab or ancestor::tei:p) and                        not( ancestor::tei:floatingText                           | parent::tei:exemplum                           | parent::tei:item                           | parent::tei:note                           | parent::tei:q                           | parent::tei:quote                           | parent::tei:remarks                           | parent::tei:said                           | parent::tei:sp                           | parent::tei:stage                           | parent::tei:cell                           | parent::tei:figure )"> Abstract model violation: Paragraphs may not occur inside other paragraphs or &lt;ab&gt; elements.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-abstractModel-structure-p-in-l-11">
      <rule context="tei:l//tei:p">
         <assert test="ancestor::tei:floatingText | parent::tei:figure | parent::tei:note"> Abstract model violation: Metrical lines (&lt;l&gt; elements) may not contain higher-level structural elements such as &lt;div&gt;, &lt;p&gt;, or &lt;ab&gt;, unless &lt;p&gt; is a child of &lt;figure&gt; or &lt;note&gt;, or is a descendant of &lt;floatingText&gt;.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-deprecationInfo-only-in-deprecated-12">
      <rule context="tei:desc[ @type eq 'deprecationInfo']">
         <assert test="../@validUntil">Information about a deprecation should only be present in a specification element that is being deprecated: that is, only an element that has a @validUntil attribute should have a child &lt;desc type="deprecationInfo"&gt;.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-rt-target-not-span-13">
      <rule context="tei:rt/@target">
         <report test="../@from | ../@to">When @target is present, neither @from nor @to should be.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-rt-from-14">
      <rule context="tei:rt/@from">
         <assert test="../@to">When @from is present, the @to attribute of &lt;<name/>&gt; is required.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-rt-to-15">
      <rule context="tei:rt/@to">
         <assert test="../@from">When @to is present, the @from attribute of &lt;<name/>&gt; is required.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-ptrAtts-16">
      <rule context="tei:ptr">
         <report test="@target and @cRef">Only one of the attributes @target and @cRef may be supplied on &lt;<name/>&gt;.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-refAtts-17">
      <rule context="tei:ref">
         <report test="@target and @cRef">Only one of the attributes @target and @cRef may be supplied on &lt;<name/>&gt;.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-gloss-list-must-have-labels-18">
      <rule context="tei:list[@type='gloss']">
         <assert test="tei:label"> The content of a "gloss" list should include a sequence of one or more pairs of a &lt;label&gt; element followed by an &lt;item&gt; element.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-targetorcontent1-19">
      <rule context="tei:relatedItem">
         <report test="@target and count( child::* ) &gt; 0">If the @target attribute on <name/> is used, the relatedItem element must be empty.</report>
         <assert test="@target or child::*">A relatedItem element should have either a @target attribute or a child element to indicate the related bibliographic item.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-abstractModel-structure-l-in-l-20">
      <rule context="tei:l">
         <report test="ancestor::tei:l[not(.//tei:note//tei:l[. = current()])]">Abstract model violation: Metrical lines (&lt;l&gt; elements) may not contain &lt;l&gt; or &lt;lg&gt; elements.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-atleast1oflggapl-21">
      <rule context="tei:lg">
         <assert test="count(descendant::tei:lg|descendant::tei:l|descendant::tei:gap) &gt; 0">An &lt;lg&gt; element must contain at least one child &lt;l&gt;, &lt;lg&gt;, or &lt;gap&gt; element.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-abstractModel-structure-lg-in-l-22">
      <rule context="tei:lg">
         <report test="ancestor::tei:l[not(.//tei:note//tei:lg[. = current()])]">Abstract model violation: Lines may not contain line groups.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-abstractModel-structure-div-in-l-23">
      <rule context="tei:l//tei:div">
         <assert test="ancestor::tei:floatingText"> Abstract model violation: Metrical lines (&lt;l&gt; elements) may not contain higher-level structural elements such as &lt;div&gt;, unless &lt;div&gt; is a descendant of &lt;floatingText&gt;.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-abstractModel-structure-div-in-ab-or-p-24">
      <rule context="tei:div">
         <report test="(ancestor::tei:p or ancestor::tei:ab) and not(ancestor::tei:floatingText)"> Abstract model violation: &lt;p&gt; and &lt;ab&gt; may not contain higher-level structural elements such as &lt;div&gt;, unless &lt;div&gt; is a descendant of &lt;floatingText&gt;.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-pathmustnotbeclosed-25">
      <rule context="tei:path[@points]">
         <let name="firstPair" value="tokenize( normalize-space( @points ), ' ')[1]"/>
         <let name="lastPair"
              value="tokenize( normalize-space( @points ), ' ')[last()]"/>
         <let name="firstX" value="xs:float( substring-before( $firstPair, ',') )"/>
         <let name="firstY" value="xs:float( substring-after( $firstPair, ',') )"/>
         <let name="lastX" value="xs:float( substring-before( $lastPair, ',') )"/>
         <let name="lastY" value="xs:float( substring-after( $lastPair, ',') )"/>
         <report test="$firstX eq $lastX and $firstY eq $lastY">The first and last elements of this path are the same. To specify a closed polygon, use the &lt;zone&gt; element rather than the &lt;path&gt; element.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-abstractModel-structure-ab-in-l-26">
      <rule context="tei:l//tei:ab">
         <assert test="ancestor::tei:floatingText | parent::tei:figure | parent::tei:note"> Abstract model violation: Metrical lines (&lt;l&gt; elements) may not contain higher-level divisions such as &lt;p&gt; or &lt;ab&gt;, unless &lt;ab&gt; is a child of &lt;figure&gt; or &lt;note&gt;, or is a descendant of &lt;floatingText&gt;.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-noNestedS-27">
      <rule context="tei:s">
         <report test="tei:s">You may not nest one &lt;s&gt; element within another: use &lt;seg&gt; instead.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-one_ms_singleton_max-28">
      <rule context="tei:msContents|tei:physDesc|tei:history|tei:additional">
         <let name="gi" value="name(.)"/>
         <report test="preceding-sibling::*[ name(.) eq $gi ]                           and                           not( following-sibling::*[ name(.) eq $gi ] )"> Only one &lt;<name/>&gt; is allowed as a child of &lt;<value-of select="name(..)"/>&gt;.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-msId_minimal-29">
      <rule context="tei:msIdentifier">
         <report test="not( parent::tei:msPart )                           and                           ( child::*[1]/self::idno  or  child::*[1]/self::altIdentifier  or  normalize-space(.) eq '')">An &lt;msIdentifier&gt; must contain either a &lt;repository&gt; or &lt;location&gt;.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-langUsage-langues-seules-30">
      <rule context="tei:langUsage">
         <report test="tei:language and (tei:p or tei:ab)"> langUsage mixes prose and language elements; its content model is (model.pLike+ | language+). Prose about the languages belongs in encodingDesc.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-language-quantite-31">
      <rule context="tei:langUsage/tei:language">
         <assert test="@ident and normalize-space(@ident)"> A language element must carry a non-empty @ident.</assert>
         <assert test="matches(@n, '^[0-9]+ (words|containers)$')"> @n on language must be a count and its unit, e.g. "1716 words" or "38 containers"; found "<value-of select="@n"/>".</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-choice-orig-reg-32">
      <rule context="tei:choice">
         <assert test="count(tei:orig) = 1 and count(tei:reg) = 1"> A choice produced by modernization holds exactly one orig and one reg.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-pas-de-cesure-residuelle-33">
      <rule context="tei:reg">
         <report test="contains(., '¬')"> A residual line-break hyphen (¬) in a modernized form: "<value-of select="normalize-space(.)"/>".</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-entite-automatique-datee-34">
      <rule context="*[@resp = '#ner-auto']">
         <assert test="@cert"> An automatically detected entity must carry @cert (high, medium or low).</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-graphiczone-identifiee-35">
      <rule context="tei:zone[@type = 'GraphicZone']">
         <assert test="@xml:id"> A GraphicZone must carry an xml:id: its figure in the text refers to it by that identifier.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-figure-ancree-36">
      <rule context="tei:figure">
         <assert test="@corresp"> A figure must carry @corresp naming the GraphicZone it comes from.</assert>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-idno-iiif-unique-37">
      <rule context="tei:idno[@type = 'iiif']">
         <report test="contains(., '|')"> An unsplit IIIF idno: several manifests share one element.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-pas-de-gabarit-publie-38">
      <rule context="tei:ptr[@target] | tei:idno">
         <report test="contains(@target, '0000-0000-0000-0000')                                   or contains(., '0000-0000-0000-0000')"> A placeholder ORCID reached the output.</report>
      </rule>
   </pattern>
   <pattern id="schematron-constraint-inventaire-ferme-39">
      <rule context="tei:*">
         <assert test="local-name() = (                     'TEI', 'ab', 'addName', 'altIdentifier', 'appInfo', 'application',                     'author', 'authority', 'availability', 'bibl', 'birth', 'body',                     'c', 'catDesc', 'category', 'change', 'choice', 'classDecl',                     'country', 'creation', 'date', 'death', 'div', 'editorialDecl',                     'education', 'encodingDesc', 'extent', 'faith', 'fileDesc', 'figure',                     'foreign', 'forename', 'front', 'fw', 'genName', 'graphic',                     'head', 'hi', 'idno', 'interpretation', 'keywords', 'label',                     'langUsage', 'language', 'lb', 'licence', 'line', 'listPerson',                     'material', 'measure', 'msDesc', 'msIdentifier', 'name', 'nameLink',                     'note', 'objectDesc', 'objectName', 'occupation', 'orgName', 'orig',                     'p', 'particDesc', 'path', 'pb', 'pc', 'persName',                     'person', 'physDesc', 'placeName', 'profileDesc', 'ptr', 'pubPlace',                     'publicationStmt', 'publisher', 'reg', 'repository', 'resp', 'respStmt',                     'revisionDesc', 'roleName', 'rs', 's', 'settlement', 'sourceDesc',                     'sourceDoc', 'surface', 'surname', 'taxonomy', 'teiHeader', 'term',                     'text', 'textClass', 'title', 'titlePage', 'titlePart', 'titleStmt',                     'w', 'zone')"> Element "<value-of select="local-name()"/>" is not part of the ALTO2TEI inventory. Either the pipeline gained an element and schema/alto2tei.odd has not caught up, or this is a stray.</assert>
      </rule>
   </pattern>
</schema>
