import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from lib.css_differ import css_diff, POSITIONAL_RESET_PROPS


def test_same_value_goes_to_base_only():
    base, resp = css_diff({'display': 'flex'}, {'display': 'flex'})
    assert base == {'display': 'flex'}
    assert resp == {}


def test_different_value_goes_to_both():
    base, resp = css_diff(
        {'flexDirection': 'row'},
        {'flexDirection': 'column'},
    )
    assert base == {'flexDirection': 'row'}
    assert resp == {'flexDirection': 'column'}


def test_base_only_prop_stays_in_base():
    base, resp = css_diff({'gap': '24px'}, {})
    assert base == {'gap': '24px'}
    assert 'gap' not in resp


def test_positional_prop_base_only_gets_unset():
    base, resp = css_diff({'position': 'absolute', 'left': '200px', 'top': '100px'}, {})
    assert base['position'] == 'absolute'
    assert resp['left'] == 'unset'
    assert resp['top'] == 'unset'


def test_non_positional_prop_base_only_no_unset():
    # gap and font-size are base-only props that should NOT generate responsive overrides.
    # (padding is intentionally excluded here: it is now in _VISUAL_RESET_PROPS so
    #  base-only padding resets to 0 in H5 — covered by test_u427.)
    base, resp = css_diff({'gap': '24px', 'font-size': '16px'}, {})
    assert 'gap' not in resp
    assert 'font-size' not in resp


def test_supplement_only_prop_goes_to_responsive():
    base, resp = css_diff({}, {'fontSize': '14px'})
    assert 'fontSize' not in base
    assert resp == {'fontSize': '14px'}


def test_fixed_width_converted_to_max_width():
    base, resp = css_diff({'width': '1440px'}, {'width': '393px'})
    assert base['max-width'] == '1440px'
    assert base['width'] == '100%'
    assert 'width' not in resp  # H5 固定宽度不搬运，100% 已足够


def test_non_px_width_not_converted():
    base, resp = css_diff({'width': '100%'}, {'width': '90%'})
    assert 'max-width' not in base
    assert base['width'] == '100%'
    assert resp['width'] == '90%'


def test_positional_reset_props_contains_expected():
    for prop in ('position', 'top', 'left', 'right', 'bottom', 'transform', 'zIndex'):
        assert prop in POSITIONAL_RESET_PROPS


def test_pc_margin_left_resets_to_zero_when_h5_lacks_it():
    """css_diff: when PC has margin-left but H5 doesn't, responsive should override to 0px.
    Real data: EU deposit campaign hero-content-wrapper (250:2623).
    PC has margin-left: 120px (Figma canvas layout), H5 content has no margin-left.
    Without this fix, margin-left: 120px stays on mobile, pushing content off-screen.
    Fix: PC-only margin-* and max-* properties reset to 0px/100% in responsive overrides.
    """
    pc_css = {'margin-left': '120px', 'margin-top': '203px', 'max-width': '627px',
              'display': 'flex', 'flex-direction': 'column'}
    h5_css = {'display': 'flex', 'flex-direction': 'column', 'align-items': 'center'}
    base, resp = css_diff(pc_css, h5_css)
    # PC margins should be reset on mobile when H5 doesn't have them
    assert resp.get('margin-left') == '0px', (
        f'margin-left:120px should reset to 0px in responsive when H5 lacks it. Got: {resp}'
    )
    assert resp.get('margin-top') == '0px', (
        f'margin-top:203px should reset to 0px in responsive when H5 lacks it. Got: {resp}'
    )
    assert resp.get('max-width') == '100%', (
        f'max-width:627px should reset to 100% in responsive when H5 lacks it. Got: {resp}'
    )


def test_u425_pc_button_visual_props_reset_when_h5_is_text_only():
    """U-425: css_diff should reset visual button properties (height, background-color,
    border-radius, padding) in H5 when PC has them but H5 node lacks them entirely.

    Real data: node 250:2631 (by_primary-buttons-dark) from EU deposit campaign hero.
    PC: height=48px, padding=12px 28px, background-color=#ff9c2e, border-radius=24px.
    H5 equivalent (I250:3845;513:18871): text properties only, no button dimensions.
    Without resets: outer container gets H5 button styles BUT inner keeps PC height=48px
    → outer height = padding(12+12) + inner(48) = 72px instead of 48px.
    """
    pc_css = {
        'height': '48px',
        'padding': '12px 28px 12px 28px',
        'background-color': '#ff9c2e',
        'border-radius': '24px',
        'width': '100%',
        'display': 'flex',
        'flex-direction': 'row',
        'align-items': 'center',
    }
    h5_css = {
        'flex-shrink': '0',
        'color': 'var(--bds-static-black)',
        'font-size': '16px',
        'font-weight': '600',
        'font-family': "'Inter', sans-serif",
        'line-height': '24px',
    }
    base, resp = css_diff(pc_css, h5_css)

    assert resp.get('height') == 'unset', (
        f'U-425 FAIL: height:48px should reset to unset in H5 when H5 lacks it. Got: {resp.get("height")}'
    )
    assert resp.get('background-color') == 'transparent', (
        f'U-425 FAIL: background-color:#ff9c2e should reset to transparent in H5. Got: {resp.get("background-color")}'
    )
    assert resp.get('border-radius') == '0', (
        f'U-425 FAIL: border-radius:24px should reset to 0 in H5 when H5 lacks it. Got: {resp.get("border-radius")}'
    )
    # padding not required to reset here — handled separately via compound-value detection


def test_u428_pc_nowrap_reset_to_normal_when_h5_lacks_whitespace():
    """U-428: css_diff should reset PC white-space:nowrap to normal in H5 when H5 lacks it.

    Real data: node 250:2668 (task-name in OriginalCopySection), EU deposit campaign.
    PC: white-space=nowrap (Figma single-line text node on 1440px desktop).
    H5 equivalent 250:3884: no white-space property — the text should wrap on 390px mobile.
    Bug: white-space is base-only (Case 4) with no reset → PC nowrap persists → text
    overflows the 284px container instead of wrapping to multiple lines.
    Fix: add white-space to _VISUAL_RESET_PROPS so base-only nowrap resets to 'normal'.
    """
    pc_css = {
        'font-size': '16px',
        'font-weight': '500',
        'white-space': 'nowrap',
        'width': '100%',
        'max-width': '496px',
        'line-height': '24px',
    }
    h5_css = {
        'font-size': '16px',
        'font-weight': '500',
        'width': '100%',
        'align-self': 'stretch',
        'line-height': '24px',
    }
    base, resp = css_diff(pc_css, h5_css)

    assert resp.get('white-space') == 'normal', (
        f'U-428 FAIL: white-space:nowrap should reset to normal in H5 when H5 lacks it. '
        f'Bug: text overflows 284px container on mobile instead of wrapping. '
        f'Got: {resp.get("white-space")!r}'
    )


def test_u427_pc_padding_reset_when_h5_is_text_only():
    """U-427: css_diff should reset PC padding to 0 in H5 when H5 lacks padding entirely.

    Real data: node 250:2631 (by_primary-buttons-dark) in EU deposit hero.
    PC: padding=12px 28px 12px 28px (button inset).
    H5 equivalent: text-only, no padding property at all.
    Bug: padding is base-only (Case 4) with no reset → PC padding persists in H5 →
    outer hero-cta-row padding(12+12=24) + inner button padding(12+12=24) + text(24) = 72px
    instead of Figma's 48px (outer padding 24 + text 24 = 48).
    Fix: add padding to _VISUAL_RESET_PROPS so base-only padding resets to '0' in H5.
    """
    pc_css = {
        'height': '48px',
        'padding': '12px 28px 12px 28px',
        'background-color': '#ff9c2e',
        'border-radius': '24px',
        'display': 'flex',
        'align-items': 'center',
    }
    h5_css = {
        'flex-shrink': '0',
        'color': 'var(--bds-static-black)',
        'font-size': '16px',
        'font-weight': '600',
        'line-height': '24px',
    }
    base, resp = css_diff(pc_css, h5_css)

    assert resp.get('padding') == '0', (
        f'U-427 FAIL: padding:12px28px should reset to 0 in H5 when H5 lacks it. '
        f'Bug: base-only padding persists, causing double-padding inside hero button. '
        f'Got: {resp.get("padding")!r}'
    )


def test_u426_h5_fixed_px_width_preserved_when_pc_also_fixed_px():
    """U-426: css_diff must preserve H5 fixed-px width in responsive when PC also has fixed px.

    Real data: EU deposit campaign EarnFromSection2 logo tiles.
    PC node 250:3309: width=169.71px (fixed).  H5 node 250:4514: width=48px (fixed).
    Bug: the 'Convert fixed root width' end-block did responsive.pop('width', None)
    unconditionally, erasing H5's 48px and leaving the tile at 100% in mobile.
    Fix: only pop responsive['width'] when H5 width is NOT a distinct fixed-px value.
    """
    pc_css = {
        'width': '169.71px',
        'height': '47.14px',
        'flex-shrink': '0',
        'position': 'relative',
    }
    h5_css = {
        'width': '48px',
        'height': '48px',
        'flex-shrink': '0',
        'background-color': 'var(--bds-gray-ele-line)',
        'border-radius': '9.6px',
        'overflow': 'hidden',
        'position': 'relative',
    }
    base, resp = css_diff(pc_css, h5_css)

    # PC fixed width should be converted to max-width + 100%
    assert base.get('width') == '100%', f'U-426 FAIL: base width should be 100%, got {base.get("width")}'
    assert base.get('max-width') == '169.71px', f'U-426 FAIL: base max-width should be 169.71px, got {base.get("max-width")}'

    # H5 fixed-px width must survive in responsive (not erased by the pop)
    assert resp.get('width') == '48px', (
        f'U-426 FAIL: H5 width:48px must be in responsive. '
        f'Bug: "Convert fixed root width" block pops responsive["width"] even when H5 has a distinct px value. '
        f'Got responsive: {resp}'
    )


def test_u431_h5_percentage_width_cancels_pc_max_width_cap():
    """U-431: when PC has fixed-px width and H5 has percentage width, responsive must
    include max-width:none to cancel the PC max-width cap generated by the conversion block.

    Real data: EU deposit campaign earn-steps text nodes (250:2640 etc.).
    PC: width=228px (text fits on 1440px desktop in one line).
    H5: width=100% text-align=center (text should span full parent on 353px mobile).

    Bug: 'Convert fixed root width' block converts PC width:228px →
    base:{width:100%, max-width:228px}. Then it pops H5 width:100% from responsive
    (because _is_small_fixed_px('100%')=False), leaving responsive={}.
    In H5: max-width:228px persists, capping the text at 228px in a 353px container.
    text-align:center applies over just 228px not 353px → text appears left-biased.

    Fix: when H5 has a flexible/percentage width and no explicit max-width override,
    add max-width:none to responsive to cancel the PC cap.
    """
    pc_css = {
        'width': '228px',
        'text-align': 'center',
        'font-size': '18px',
        'color': '#383b3d',
    }
    h5_css = {
        'width': '100%',
        'text-align': 'center',
        'font-size': '18px',
        'color': '#383b3d',
    }
    base, resp = css_diff(pc_css, h5_css)

    # PC width should be converted to max-width + 100%
    assert base.get('width') == '100%', f'U-431 FAIL: base width should be 100%, got {base.get("width")}'
    assert base.get('max-width') == '228px', f'U-431 FAIL: base max-width should be 228px, got {base.get("max-width")}'

    # H5 must cancel the PC max-width cap so the text spans the full mobile container
    assert resp.get('max-width') == 'none', (
        f'U-431 FAIL: max-width:none must be in responsive to cancel PC cap of 228px. '
        f'Bug: text is stuck at max-width:228px in a 353px H5 container → text-align:center '
        f'operates over 228px, making text appear left-biased. '
        f'Got responsive: {resp}'
    )


def test_u434_text_align_left_not_added_to_responsive_when_pc_is_flex_centered():
    """U-434: css_diff must NOT add supplement-only text-align:left to responsive when
    the PC base is a flex container with justify-content:center.

    Real data: EU deposit campaign hero CTA button node 250:2631 (by_primary-buttons-dark).
    PC: display:flex; justify-content:center (button uses flex centering, no text-align).
    H5 supplement: text-align:left (text-only button, Figma default for text nodes).

    Bug: css_diff Case 5 (supplement-only) adds text-align:left to responsive. In H5, the
    outer hero-cta-row becomes the orange pill button (width:100%); by_primary-buttons-dark
    inside it is also width:100%. text-align:left within a 100%-wide flex child makes
    "Register Now" appear at the far left instead of centered.

    Fix: when base is display:flex + justify-content:center, skip supplement-only
    text-align:left — flex centering handles visual alignment, not text-align.
    """
    pc_css = {
        'display': 'flex',
        'justify-content': 'center',
        'align-items': 'center',
        'width': '100%',
        'height': '48px',
        'padding': '12px 28px 12px 28px',
        'background-color': '#ff9c2e',
        'border-radius': '24px',
    }
    h5_css = {
        'text-align': 'left',
        'font-size': '16px',
        'color': 'var(--bds-static-black)',
        'font-weight': '600',
        'line-height': '24px',
    }
    _, resp = css_diff(pc_css, h5_css)

    assert 'text-align' not in resp, (
        f'U-434 FAIL: text-align:left must NOT be added to responsive when PC base is '
        f'display:flex + justify-content:center. '
        f'Bug: "Register Now" button text appears left-aligned on H5 because '
        f'text-align:left overrides flex centering in a 100%-wide child. '
        f'Got responsive: {resp}'
    )


if __name__ == '__main__':
    tests = [
        test_same_value_goes_to_base_only,
        test_different_value_goes_to_both,
        test_base_only_prop_stays_in_base,
        test_positional_prop_base_only_gets_unset,
        test_non_positional_prop_base_only_no_unset,
        test_supplement_only_prop_goes_to_responsive,
        test_fixed_width_converted_to_max_width,
        test_non_px_width_not_converted,
        test_positional_reset_props_contains_expected,
        test_pc_margin_left_resets_to_zero_when_h5_lacks_it,
        test_u425_pc_button_visual_props_reset_when_h5_is_text_only,
        test_u428_pc_nowrap_reset_to_normal_when_h5_lacks_whitespace,
        test_u427_pc_padding_reset_when_h5_is_text_only,
        test_u426_h5_fixed_px_width_preserved_when_pc_also_fixed_px,
        test_u431_h5_percentage_width_cancels_pc_max_width_cap,
        test_u434_text_align_left_not_added_to_responsive_when_pc_is_flex_centered,
    ]
    passed = 0
    for t in tests:
        try:
            t(); print(f'  ✓ {t.__name__}'); passed += 1
        except Exception as e:
            print(f'  ✗ {t.__name__}: {e}')
    print(f'\n{passed}/{len(tests)} passed')
