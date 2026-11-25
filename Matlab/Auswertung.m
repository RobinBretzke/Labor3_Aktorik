% Auswertung Labor3 Aktorik
g1 = 50;         % g
n_g1 = 133;      % 1/min
I1=0.2;          %A

g2 = 100;        % g
n_g2 = 104;      % 1/min
I2=0.29;         %A

n_ll = 171;
d_spule = 60;    % mm
g = 10;          % m/s^2
U=5;             %V

M1 = g1*0.001 * g * (d_spule/2)*0.001;
M2 = g2*0.001 * g * (d_spule/2)*0.001;

P_el1=I1*U;
P_el2=I2*U;

P_mech1=(n_g1*2*pi/60)*M1;
P_mech2=(n_g2*2*pi/60)*M2;

eta_1=P_mech1/P_el1;
eta_2=P_mech2/P_el2;

x = [n_ll, n_g1, n_g2];
y = [0,    M1,   M2  ];

p = polyfit(x, y, 1);
n_lin = linspace(0, n_ll, 1000);
M_lin = polyval(p, n_lin);
P = (n_lin .* 2.*pi./60) .* M_lin;

% Optimalen Arbeitspunkt bestimmen
[P_max, idx] = max(P);
n_opt = n_lin(idx);
M_opt = M_lin(idx);

% Linke Achse: Drehmoment
yyaxis left
plot(n_lin, M_lin, 'b-', 'DisplayName', 'Ausgleichsgerade');
hold on
scatter(x, y, 60, 'b', 'filled', 'DisplayName', 'Messpunkte');
scatter(n_opt, M_opt, 120, 'k', 'filled', 'DisplayName', 'Opt. Arbeitspunkt');
ylabel('Drehmoment M (N·m)');
ylim([0, max(M_lin)*1.3]);

% Rechte Achse: Leistung
yyaxis right
plot(n_lin, P, 'r--', 'DisplayName', 'Leistung P');
scatter(n_opt, P_max, 120, 'k', 'filled', 'HandleVisibility', 'off');
% Hilfslinie zum Arbeitspunkt
xline(n_opt, 'k:', 'HandleVisibility', 'off');
ylabel('Leistung P (W)');
ylim([0, max(P)*1.3]);

% Beschriftung des Punktes
text(n_opt + 2, P_max * 0.95, ...
    sprintf('P_{max} = %.4f W\nn_{opt} = %.1f min^{-1}', P_max, n_opt), ...
    'FontSize', 9, 'Color', 'k');

annotation('textbox', [0.15, 0.73, 0.15, 0.1], ...
    'String', sprintf('\\eta_1 = %.1f%%\n\\eta_2 = %.1f%%', eta_1*100, eta_2*100), ...
    'FontSize', 9, 'EdgeColor', 'none', 'Interpreter', 'tex');

xlabel('Drehzahl n (min^{-1})');
xlim([0, n_ll+10]);
legend('Location', 'northwest');
grid on
title('n-M-Kennlinie und Leistungskurve');