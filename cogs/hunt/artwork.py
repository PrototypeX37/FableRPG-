"""Artwork for each named Hunt variant, hosted at the supplied public URLs."""

_ARTWORK_BASE_URL = (
    "https://pub-0e7afc36364b4d5dbd1fd2bea161e4d1.r2.dev/"
    "zipuploads/295173706496475136/"
)

# Keys match generated beast names. Keep uploaded filenames verbatim, including
# Thunder/Thundering, lowercase leviathan, and the VileWebdigo filename typo.
_ARTWORK_FILES = {
    "ancient behemoth": "99cf7ce7e9ea4df68e04b566d514f366_Ancient%20Behemoth.png",
    "ancient chimera": "c88fb08aa5b24f9693d0ebd81700fb51_AncientChimera.png",
    "ancient leviathan": "8d3ffe313419479c9685af1ee00ce273_Ancientleviathan.png",
    "ancient manticore": "ec8a7903fff545479706e6ba084cba07_AncientManticore.png",
    "ancient wendigo": "036ea1394f354fe585c096dec972fbb1_AncientWendigo.png",
    "dread behemoth": "ac753e6b286047939754766d1f597cb1_DreadBehemoth.png",
    "dread chimera": "71c95a0447284fa895a08243e518ffc5_DreadChimera.png",
    "dread leviathan": "74f025fe96e8472cb897abd87e459f0f_Dreadleviathan.png",
    "dread manticore": "4c44a1b554494ae2ab916bf32fc57d93_DreadManticore.png",
    "dread wendigo": "fde9607ac4db43cea6c2d537bd5c2f1d_DreadWendigo.png",
    "pale behemoth": "10ee3476e36e415e9feeb3c306669928_Pale%20Behemoth.png",
    "pale chimera": "2929b602126542c7b2a4a0cc651b01ca_PaleChimera.png",
    "pale leviathan": "1e1304b7ec374473aa6e09148c2efd8a_Paleleviathan.png",
    "pale manticore": "625851d97e4b49f7b3e7fd519efbba97_PaleManticore.png",
    "pale wendigo": "75f330badfc14936aeec2f7642bc6579_PaleWendigo.png",
    "thundering behemoth": "681457fe542e449abcc071514cf8579c_Thundering%20Behemoth.png",
    "thundering chimera": "4f984d13e0a647bcaf276a5c847359be_ThunderChimera.png",
    "thundering leviathan": "8773d8ab720f407b9ec94f94e0dccc8c_Thunderleviathan.png",
    "thundering manticore": "9b38e4403a8e434792673a6cd5816faa_ThunderManticore.png",
    "thundering wendigo": "5052e273a59144e5a6a128ce505769cc_ThunderWendigo.png",
    "vile behemoth": "7903dd9a9a824154bf251caeab782e25_Vile%20Behemoth.png",
    "vile chimera": "a6fde61ace3b465c9c7d5338050676ef_VileChimera.png",
    "vile leviathan": "529403c799dc413ba7fa3df1ea39fe0b_Vileleviathan.png",
    "vile manticore": "90dd9203145446c1bc32d70237328c30_VileManticore.png",
    "vile wendigo": "88519c59962e4df3819ba6c1f385f74e_VileWebdigo.png",
}


def beast_image_url(beast_name):
    """Resolve generated names with or without their leading 'the'."""
    normalized = " ".join(str(beast_name or "").casefold().split())
    filename = _ARTWORK_FILES.get(normalized.removeprefix("the "))
    return _ARTWORK_BASE_URL + filename if filename else None
