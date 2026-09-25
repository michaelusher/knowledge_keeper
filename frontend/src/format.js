// "section: Intro–section: Setup" -> "Intro – Setup"; "slide 4" stays as is.
export const formatLocation = (loc = "") => loc.replace(/section:\s*/g, "").replace(/–/g, " – ");
