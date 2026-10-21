"""Resource anchor mirroring the monolith package layout.

The emotion reply resolver locates its default policy file relative to this
package's directory; keeping the anchor package (and the bundled policy JSON)
here preserves that lookup when the memory ontology engines themselves stay
monolith-side.
"""
