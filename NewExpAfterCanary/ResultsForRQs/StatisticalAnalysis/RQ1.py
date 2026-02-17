import numpy as np
from scipy.stats import chi2_contingency
import pandas as pd

def cohens_h(p1, p2):
    """
    Calculate Cohen's h effect size for two proportions.
    
    Interpretation:
    - h < 0.2: Small effect (minimal practical difference)
    - h = 0.5: Medium effect (noticeable difference)
    - h > 0.8: Large effect (substantial difference)
    """
    return abs(2 * (np.arcsin(np.sqrt(p1)) - np.arcsin(np.sqrt(p2))))

def chi_square_test(group1_success, group1_total, group2_success, group2_total, 
                    group1_name="Group 1", group2_name="Group 2"):
    """
    Perform chi-square test comparing two groups.
    
    Returns: chi2 statistic, p-value, and interpretation
    """
    # Create contingency table
    group1_fail = group1_total - group1_success
    group2_fail = group2_total - group2_success
    
    data = np.array([
        [group1_success, group1_fail],
        [group2_success, group2_fail]
    ])
    
    # Perform chi-square test
    chi2_stat, p_value, dof, expected = chi2_contingency(data)
    
    # Calculate proportions for Cohen's h
    p1 = group1_success / group1_total
    p2 = group2_success / group2_total
    h = cohens_h(p1, p2)
    
    # Interpretation
    if p_value < 0.001:
        sig_level = "***"
        interpretation = "Highly significant"
    elif p_value < 0.01:
        sig_level = "**"
        interpretation = "Very significant"
    elif p_value < 0.05:
        sig_level = "*"
        interpretation = "Significant"
    else:
        sig_level = "ns"
        interpretation = "Not significant"
    
    # Effect size interpretation
    if h < 0.2:
        effect_interp = "small"
    elif h < 0.5:
        effect_interp = "small-to-medium"
    elif h < 0.8:
        effect_interp = "medium-to-large"
    else:
        effect_interp = "large"
    
    return {
        'chi2': chi2_stat,
        'p_value': p_value,
        'cohens_h': h,
        'significance': sig_level,
        'interpretation': interpretation,
        'effect_size': effect_interp,
        'p1': p1,
        'p2': p2
    }

# ============================================================================
#  RQ1 DATA
# ============================================================================

data = {
    'GPT-4o': {
        'Minimal': {'generated': 5790, 'compiled': 1014, 'passed': 418},
        'Method':  {'generated': 5790, 'compiled': 1684, 'passed': 768},
        'Class':   {'generated': 5790, 'compiled': 1868, 'passed': 973}
    },
    'Qwen-480B': {
        'Minimal': {'generated': 5790, 'compiled': 1721, 'passed': 885},
        'Method':  {'generated': 5790, 'compiled': 1802, 'passed': 1028},
        'Class':   {'generated': 5790, 'compiled': 1939, 'passed': 1296}
    },
    'GPT-OSS-120B': {
        'Minimal': {'generated': 5790, 'compiled': 117, 'passed': 24},
        'Method':  {'generated': 5790, 'compiled': 199, 'passed': 108},
        'Class':   {'generated': 5790, 'compiled': 214, 'passed': 151}
    }
}

# ============================================================================
# ANALYSIS 1: COMPILATION RATES - MINIMAL vs CLASS
# ============================================================================

print("="*80)
print("ANALYSIS 1: Does Class variant improve COMPILATION over Minimal?")
print("="*80)
print()

for model in ['GPT-4o', 'Qwen-480B', 'GPT-OSS-120B']:
    minimal = data[model]['Minimal']
    class_v = data[model]['Class']
    
    result = chi_square_test(
        minimal['compiled'], minimal['generated'],
        class_v['compiled'], class_v['generated'],
        "Minimal", "Class"
    )
    
    print(f"{model}:")
    print(f"  Minimal: {minimal['compiled']}/{minimal['generated']} = {result['p1']:.1%}")
    print(f"  Class:   {class_v['compiled']}/{class_v['generated']} = {result['p2']:.1%}")
    print(f"  Improvement: {result['p2'] - result['p1']:.1%} ({(result['p2']/result['p1'] - 1)*100:.1f}% relative)")
    print(f"  Chi-square: χ² = {result['chi2']:.2f}, p = {result['p_value']:.2e} {result['significance']}")
    print(f"  Effect size: h = {result['cohens_h']:.3f} ({result['effect_size']})")
    print(f"  Conclusion: {result['interpretation']} - Class is better than Minimal")
    print()

# ============================================================================
# ANALYSIS 2: COMPILATION RATES - MINIMAL vs METHOD vs CLASS (all together)
# ============================================================================

print("="*80)
print("ANALYSIS 2: Do variants differ OVERALL in compilation? (3-way comparison)")
print("="*80)
print()

for model in ['GPT-4o', 'Qwen-480B', 'GPT-OSS-120B']:
    minimal = data[model]['Minimal']
    method = data[model]['Method']
    class_v = data[model]['Class']
    
    # Create 3x2 contingency table
    data_3way = np.array([
        [minimal['compiled'], minimal['generated'] - minimal['compiled']],
        [method['compiled'], method['generated'] - method['compiled']],
        [class_v['compiled'], class_v['generated'] - class_v['compiled']]
    ])
    
    chi2_stat, p_value, dof, expected = chi2_contingency(data_3way)
    
    if p_value < 0.001:
        sig = "***"
    elif p_value < 0.01:
        sig = "**"
    elif p_value < 0.05:
        sig = "*"
    else:
        sig = "ns"
    
    print(f"{model}:")
    print(f"  Minimal: {minimal['compiled']}/{minimal['generated']} = {minimal['compiled']/minimal['generated']:.1%}")
    print(f"  Method:  {method['compiled']}/{method['generated']} = {method['compiled']/method['generated']:.1%}")
    print(f"  Class:   {class_v['compiled']}/{class_v['generated']} = {class_v['compiled']/class_v['generated']:.1%}")
    print(f"  Chi-square: χ² = {chi2_stat:.2f}, p = {p_value:.2e} {sig}")
    print(f"  Conclusion: Variants {'differ significantly' if p_value < 0.05 else 'do not differ significantly'}")
    print()

# ============================================================================
# ANALYSIS 3: PASS RATES (among compiled tests) - MINIMAL vs CLASS
# ============================================================================

print("="*80)
print("ANALYSIS 3: Does Class variant improve PASS RATE (among compiled tests)?")
print("="*80)
print()

for model in ['GPT-4o', 'Qwen-480B', 'GPT-OSS-120B']:
    minimal = data[model]['Minimal']
    class_v = data[model]['Class']
    
    result = chi_square_test(
        minimal['passed'], minimal['compiled'],
        class_v['passed'], class_v['compiled'],
        "Minimal", "Class"
    )
    
    print(f"{model}:")
    print(f"  Minimal: {minimal['passed']}/{minimal['compiled']} = {result['p1']:.1%}")
    print(f"  Class:   {class_v['passed']}/{class_v['compiled']} = {result['p2']:.1%}")
    print(f"  Improvement: {result['p2'] - result['p1']:.1%}")
    print(f"  Chi-square: χ² = {result['chi2']:.2f}, p = {result['p_value']:.2e} {result['significance']}")
    print(f"  Effect size: h = {result['cohens_h']:.3f} ({result['effect_size']})")
    print(f"  Conclusion: {result['interpretation']}")
    print()

# ============================================================================
# ANALYSIS 4: COMPARING MODELS - GPT-4o vs Qwen (Class variant)
# ============================================================================

print("="*80)
print("ANALYSIS 4: Which model is better? (GPT-4o vs Qwen, Class variant)")
print("="*80)
print()

gpt4o_class = data['GPT-4o']['Class']
qwen_class = data['Qwen-480B']['Class']

# Compilation comparison
result_comp = chi_square_test(
    gpt4o_class['compiled'], gpt4o_class['generated'],
    qwen_class['compiled'], qwen_class['generated'],
    "GPT-4o", "Qwen"
)

print("COMPILATION RATES:")
print(f"  GPT-4o: {gpt4o_class['compiled']}/{gpt4o_class['generated']} = {result_comp['p1']:.1%}")
print(f"  Qwen:   {qwen_class['compiled']}/{qwen_class['generated']} = {result_comp['p2']:.1%}")
print(f"  Chi-square: χ² = {result_comp['chi2']:.2f}, p = {result_comp['p_value']:.2e}")
print(f"  Conclusion: Qwen {'significantly better' if result_comp['p_value'] < 0.05 and result_comp['p2'] > result_comp['p1'] else 'not significantly different'}")
print()

# Pass rate comparison
result_pass = chi_square_test(
    gpt4o_class['passed'], gpt4o_class['compiled'],
    qwen_class['passed'], qwen_class['compiled'],
    "GPT-4o", "Qwen"
)

print("PASS RATES (among compiled):")
print(f"  GPT-4o: {gpt4o_class['passed']}/{gpt4o_class['compiled']} = {result_pass['p1']:.1%}")
print(f"  Qwen:   {qwen_class['passed']}/{qwen_class['compiled']} = {result_pass['p2']:.1%}")
print(f"  Chi-square: χ² = {result_pass['chi2']:.2f}, p = {result_pass['p_value']:.2e}")
print(f"  Conclusion: Qwen {'significantly better' if result_pass['p_value'] < 0.05 and result_pass['p2'] > result_pass['p1'] else 'not significantly different'}")
print()

# ============================================================================
# SUMMARY TABLE FOR PAPER
# ============================================================================

print("="*80)
print("SUMMARY TABLE: For paper")
print("="*80)
print()

results_summary = []

for model in ['GPT-4o', 'Qwen-480B', 'GPT-OSS-120B']:
    minimal = data[model]['Minimal']
    class_v = data[model]['Class']
    
    # Compilation comparison
    comp_result = chi_square_test(
        minimal['compiled'], minimal['generated'],
        class_v['compiled'], class_v['generated']
    )
    
    # Pass rate comparison
    pass_result = chi_square_test(
        minimal['passed'], minimal['compiled'],
        class_v['passed'], class_v['compiled']
    )
    
    results_summary.append({
        'Model': model,
        'Metric': 'Compilation',
        'Minimal': f"{minimal['compiled']/minimal['generated']:.1%}",
        'Class': f"{class_v['compiled']/class_v['generated']:.1%}",
        'χ²': f"{comp_result['chi2']:.1f}",
        'p-value': f"{comp_result['p_value']:.2e}",
        "Cohen's h": f"{comp_result['cohens_h']:.2f}"
    })
    
    results_summary.append({
        'Model': model,
        'Metric': 'Pass Rate',
        'Minimal': f"{minimal['passed']/minimal['compiled']:.1%}",
        'Class': f"{class_v['passed']/class_v['compiled']:.1%}",
        'χ²': f"{pass_result['chi2']:.1f}",
        'p-value': f"{pass_result['p_value']:.2e}",
        "Cohen's h": f"{pass_result['cohens_h']:.2f}"
    })

df = pd.DataFrame(results_summary)
print(df.to_string(index=False))
print()

